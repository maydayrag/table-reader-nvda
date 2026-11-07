"""由使用者手動啟動，分批讀取無障礙物件，避免一次掃描整頁阻塞 NVDA。"""
from time import monotonic
import addonHandler
import api
import core
import globalPluginHandler
import globalVars
import ui
from controlTypes import Role, State
from scriptHandler import script
from utils.security import isRunningOnSecureDesktop
from winAPI.sessionTracking import isLockScreenModeActive
from logHandler import log
from .model import Node, findTables, render

addonHandler.initTranslation()


class ScanLimit(Exception):
    """資料不完整時整次停止，不顯示可能缺欄的結果。"""


def optional(obj, name, default=None):
    # 未支援的位置及跨列資訊由標準物件 API 拋出 NotImplementedError。
    try:
        return getattr(obj, name)
    except (AttributeError, NotImplementedError):
        return default


def capture(obj, counter, depth=0):
    if depth > 40 or counter[0] >= 1800:
        raise ScanLimit
    counter[0] += 1
    node = Node(source=obj)
    states = obj.states
    node.role = obj.role.name
    node.blocked = bool(states & {State.INVISIBLE, State.PROTECTED}) or obj.role == Role.EDITABLETEXT
    yield None
    if node.blocked:
        return node
    rect = optional(obj, "location")
    node.rect = tuple(rect) if rect else None
    node.rowSpan = optional(obj, "rowSpan", 1) or 1
    node.columnSpan = optional(obj, "columnSpan", 1) or 1
    child = obj.firstChild
    siblings = []
    while child is not None:
        # 某些無障礙提供者可能回傳循環關係；不讓它無限掃描。
        if any(child == previous for previous in siblings[-8:]):
            raise ScanLimit
        siblings.append(child)
        node.children.append((yield from capture(child, counter, depth + 1)))
        child = child.next
        yield None
    if not node.children:
        name, value = obj.name or "", obj.value or ""
        node.text = name if not value or value == name else (name + " " + value).strip()
        if len(node.text) > 20000:
            raise ScanLimit
        counter[1] += len(node.text)
        if counter[1] > 300000:
            raise ScanLimit
    return node


class GlobalPlugin(globalPluginHandler.GlobalPlugin):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.scanTask = None
        self.generation = 0
        self.bridge = None

    def cancel(self):
        # 舊計時回呼即使已排入佇列，也會因序號不同直接返回。
        self.generation += 1
        if self.scanTask is not None:
            self.scanTask.close()
            self.scanTask = None

    def terminate(self):
        self.cancel()
        if self.bridge is not None:
            self.bridge.detach()
            self.bridge = None
        super().terminate()

    def restricted(self):
        return globalVars.appArgs.secure or isRunningOnSecureDesktop() or isLockScreenModeActive()

    @script(description=_("辨識目前網頁的表格；掃描中再按一次可取消"), category=_("通用表格閱讀器"), gesture="kb:NVDA+control+shift+t")
    def script_readTables(self, gesture):
        self.start(False)

    @script(description=_("辨識表格並以第一列當欄位標題，僅在已確認第一列是標題時使用"), category=_("通用表格閱讀器"))
    def script_readTablesWithHeader(self, gesture):
        self.start(True)

    @script(description=_("建立或撤除目前網頁的原生表格導覽，建立後可按 T 與表格方向鍵"), category=_("通用表格閱讀器"), gesture="kb:NVDA+control+shift+y")
    def script_inPageTables(self, gesture):
        if self.bridge is not None and self.bridge.active:
            self.bridge.detach()
            self.bridge = None
            ui.message(_("已撤除原頁表格導覽。"))
            return
        self.start(False, inPage=True)

    @script(description=_("建立原頁表格導覽，並以第一列當欄位標題"), category=_("通用表格閱讀器"))
    def script_inPageTablesWithHeader(self, gesture):
        self.start(True, inPage=True)

    def start(self, firstRowHeader, inPage=False):
        if self.restricted():
            return
        if self.scanTask is not None:
            self.cancel()
            ui.message(_("已取消表格辨識。"))
            return
        try:
            obj = api.getNavigatorObject()
            if obj is None or obj.processID != api.getFocusObject().processID:
                ui.message(_("請先將閱讀位置移到目前網頁內容。"))
                return
            # 優先限制在目前表格；否則使用瀏覽模式提供的文件根物件。
            root, ancestor = None, obj
            for unused in range(40):
                if ancestor is None:
                    break
                if ancestor.role in {Role.TABLE, Role.DOCUMENT}:
                    root = ancestor
                    break
                ancestor = ancestor.parent
            if root is None:
                interceptor = obj.treeInterceptor
                root = interceptor.rootNVDAObject if interceptor is not None else None
            if root is None or root.role not in {Role.TABLE, Role.DOCUMENT}:
                ui.message(_("請在網頁文件內執行這個指令。"))
                return
            self.inPage = inPage
            if inPage:
                import buildVersion
                from virtualBuffers import VirtualBuffer
                if (buildVersion.version_year, buildVersion.version_major) != (2026, 3):
                    ui.message(_("原頁導覽只支援 NVDA 2026.3。"))
                    return
                document = root.treeInterceptor
                if not isinstance(document, VirtualBuffer) or not document.isReady or document.passThrough:
                    ui.message(_("請先切到網頁瀏覽模式；目前文件不支援原頁表格導覽。"))
                    return
                if self.bridge is not None:
                    self.bridge.detach()
                    self.bridge = None
                self.bridgeDocument = document
            self.scanProcess = obj.processID
            self.started = monotonic()
            self.scanTask = capture(root, [0, 0])
            self.generation += 1
            ui.message(_("正在辨識表格，再按相同指令可取消。"))
            core.callLater(1, self.step, self.generation, firstRowHeader)
        except Exception:
            self.cancel()
            log.debugWarning("tableReader: unable to start scan")
            ui.message(_("無法取得網頁結構，請重新定位後再試。"))

    def step(self, generation, firstRowHeader):
        if generation != self.generation or self.scanTask is None:
            return
        try:
            if self.restricted() or api.getFocusObject().processID != self.scanProcess:
                self.cancel()
                return
            if monotonic() - self.started > 8:
                raise ScanLimit
            sliceStart = monotonic()
            for unused in range(20):
                try:
                    next(self.scanTask)
                except StopIteration as finished:
                    self.scanTask = None
                    tables = findTables(finished.value)
                    if not tables:
                        ui.message(_("沒有找到可可靠分欄的表格。網站可能未提供欄位結構，或使用合併格與不規則排列。"))
                        return
                    if self.inPage:
                        from .bridge import Bridge
                        bridge = Bridge(self.bridgeDocument, self.restricted)
                        count = bridge.mapTables(tables, firstRowHeader)
                        if not count:
                            ui.browseableMessage(
                                _("未建立原頁表格。以下只包含原因代碼與列欄位置，沒有網頁文字或金鑰。請提供這份診斷結果。") + "\n\n" + bridge.diagnosticText(),
                                title=_("原頁表格對應診斷 1.0"), closeButton=True)
                            return
                        bridge.install()
                        self.bridge = bridge
                        ui.message(_("已建立 {count} 個原頁表格。可按 T 或 Shift＋T 尋找，再用 Ctrl＋Alt＋方向鍵閱讀。頁面更新後須重新建立。").format(count=count))
                        return
                    ui.browseableMessage(render(tables, firstRowHeader), title=_("通用表格閱讀器"), isHtml=True, closeButton=True)
                    return
                if monotonic() - sliceStart > 0.012:
                    break
            core.callLater(10, self.step, generation, firstRowHeader)
        except ScanLimit:
            self.cancel()
            ui.message(_("網頁過大或結構循環，已停止。請將閱讀位置移入目標表格後再試。"))
        except Exception:
            self.cancel()
            # 記錄固定訊息，不把網頁文字、金鑰或物件內容寫入記錄。
            log.debugWarning("tableReader: scan failed or document changed")
            ui.message(_("讀取中斷，頁面可能已更新。請重新定位後再試。"))
