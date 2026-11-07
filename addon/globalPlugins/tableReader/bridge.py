"""2026.3 限定實驗：只覆寫單一文件的索引與 TextInfo，不修改 NVDA 全域類別。"""
from dataclasses import dataclass, field
from types import MethodType
import re
import addonHandler
import browseMode
import documentBase
import textInfos
from textInfos.offsets import Offsets
from controlTypes import Role, State
from .alignment import align, AlignmentFailure

addonHandler.initTranslation()


@dataclass
class Cell:
    start: int
    end: int
    row: int
    col: int


@dataclass
class MappedTable:
    identifier: str
    rows: int
    columns: int
    header: bool
    names: list
    cells: dict = field(default_factory=dict)
    start: int = 0
    end: int = 0
    snapshot: str = field(default="", repr=False)


def normalized(value):
    # 只容許無障礙樹與緩衝區的空白差異；不修正或猜測任何數字。
    return re.sub(r"\s+", "", value)


class MappingFailure(Exception):
    """只攜帶固定原因代碼，絕不攜帶來源文字或系統例外內容。"""


class Bridge:
    def __init__(self, document, restricted):
        self.document = document
        self.restricted = restricted
        self.baseInfo = document.TextInfo
        self.tables = []
        self.active = False
        self.saved = {}
        self.installed = {}
        self.handle = document.VBufHandle
        self.diagnostics = []
        self.sequenceMatches = 0

    def mapSequence(self, candidate, result):
        # 容器沒有獨立節點時，以整表唯一的完整序列定位，而非猜單一欄位的位置。
        info = self.baseInfo(self.document, textInfos.POSITION_ALL)
        if info.encoding != "utf_16_le":
            raise MappingFailure("SEQUENCE_ENCODING")
        entries = []
        for rowIndex, row in enumerate(candidate.nodes, 1):
            for colIndex, node in enumerate(row, 1):
                if node.source is None and rowIndex == 1 and colIndex == result.columns and node.text == _("操作（補入的欄名）"):
                    continue
                entries.append((rowIndex, colIndex, candidate.rows[rowIndex - 1][colIndex - 1]))
        try:
            ranges = align(info.text, [entry[2] for entry in entries])
        except AlignmentFailure as error:
            raise MappingFailure(str(error)) from None
        result.cells.clear()
        for (row, col, expected), bounds in zip(entries, ranges):
            if bounds is None:
                continue
            start, end = bounds
            cellInfo = self.baseInfo(self.document, Offsets(start, end))
            if normalized(cellInfo.text) != normalized(expected):
                raise MappingFailure("SEQUENCE_VERIFY")
            for command in cellInfo.getTextWithFields():
                if isinstance(command, textInfos.FieldCommand) and command.command == "controlStart" and command.field.get("table-id") is not None:
                    raise MappingFailure("NATIVE_TABLE")
            result.cells[row, col] = Cell(start, end, row, col)
        presentRanges = [bounds for bounds in ranges if bounds is not None]
        result.start, result.end = presentRanges[0][0], presentRanges[-1][1]
        result.snapshot = self.baseInfo(self.document, Offsets(result.start, result.end)).text
        return result

    def resolveCell(self, node, expected):
        reason = "NO_RANGE"
        try:
            info = self.baseInfo(self.document, node.source)
            if info._startOffset < info._endOffset:
                if normalized(info.text) == normalized(expected):
                    return info
                reason = "TEXT_MISMATCH"
            else:
                reason = "EMPTY_RANGE"
        except Exception:
            pass
        # 容器本身不一定在虛擬緩衝區；只嘗試其現有子文字物件，不搜尋整份網頁。
        leaves = []
        pending = list(reversed(node.children))
        while pending:
            part = pending.pop()
            if part.blocked:
                raise MappingFailure("BLOCKED_CHILD")
            if part.children:
                pending.extend(reversed(part.children))
            elif part.source is not None and part.text:
                leaves.append(part)
        if not leaves or len(leaves) > 128:
            raise MappingFailure(reason)
        ranges = []
        try:
            for part in leaves:
                leafInfo = self.baseInfo(self.document, part.source)
                left, right = leafInfo._startOffset, leafInfo._endOffset
                if left >= right or (ranges and left < ranges[-1][1]):
                    raise MappingFailure("CHILD_RANGE_ORDER")
                if normalized(leafInfo.text) != normalized(part.text):
                    raise MappingFailure("CHILD_TEXT_MISMATCH")
                ranges.append((left, right))
            info = self.baseInfo(self.document, Offsets(ranges[0][0], ranges[-1][1]))
            if normalized(info.text) != normalized(expected):
                raise MappingFailure("CHILD_COMBINED_MISMATCH")
            return info
        except MappingFailure:
            raise
        except Exception:
            raise MappingFailure("CHILD_NO_RANGE") from None

    def diagnosticText(self):
        labels = {
            "NOT_INFERRED": _("不是位置推測表格"), "TOO_LARGE": _("超過 256 格上限"),
            "NO_SOURCE": _("儲存格沒有來源物件"), "NO_RANGE": _("來源物件無法轉成網頁文字位置"),
            "EMPTY_RANGE": _("來源物件對應到空範圍"), "TEXT_MISMATCH": _("物件文字與網頁範圍文字不同"),
            "RANGE_ORDER": _("儲存格範圍重疊或順序不同"), "NATIVE_TABLE": _("範圍內已有原生表格資訊"),
            "BLOCKED_CHILD": _("子物件受保護或不可讀"), "CHILD_RANGE_ORDER": _("子物件範圍重疊或順序不同"),
            "CHILD_TEXT_MISMATCH": _("子物件文字與網頁範圍不同"), "CHILD_COMBINED_MISMATCH": _("子物件合併範圍包含額外或缺少文字"),
            "CHILD_NO_RANGE": _("子物件無法轉成網頁文字位置"), "TABLE_OVERLAP": _("與另一個候選表格範圍重疊"),
            "READ_ERROR": _("讀取網頁資訊失敗"), "NO_CELLS": _("沒有可對應的儲存格"),
            "SEQUENCE_LIMIT": _("網頁超過整表核對的長度上限"), "SEQUENCE_EMPTY": _("表格有空白格或資料不足，不能作完整序列核對"),
            "SEQUENCE_INSUFFICIENT": _("非空白格少於四格，無法可靠核對完整序列"),
            "SEQUENCE_NOT_FOUND": _("整張表格的文字序列與網頁緩衝區不相符"), "SEQUENCE_AMBIGUOUS": _("整張表格的相同文字序列出現不只一次"),
            "SEQUENCE_ENCODING": _("網頁文字位置不是支援的 UTF-16 格式"), "SEQUENCE_VERIFY": _("整表定位後的單格文字核對失敗"),
        }
        return "\n".join(_("候選 {table}，第 {row} 列、第 {col} 欄：{reason} [{code}]").format(table=t, row=r, col=c, reason=labels[code], code=code) for t,r,c,code in self.diagnostics)

    def mapTables(self, candidates, firstRowHeader=False):
        mapped = []
        self.diagnostics.clear()
        self.sequenceMatches = 0
        for candidateIndex, candidate in enumerate(candidates, 1):
            # 保留原生表格原本的實作，只補位置推測的候選。
            if not candidate.inferred or sum(map(len, candidate.nodes)) > 256:
                self.diagnostics.append((candidateIndex, 0, 0, "NOT_INFERRED" if not candidate.inferred else "TOO_LARGE"))
                continue
            result = MappedTable("tableReader:" + str(id(self)) + ":" + str(len(mapped)), len(candidate.rows), len(candidate.rows[0]), candidate.header or firstRowHeader, candidate.rows[0])
            previousEnd = -1
            rowIndex = colIndex = 0
            try:
                for rowIndex, row in enumerate(candidate.nodes, 1):
                    for colIndex, node in enumerate(row, 1):
                        if node.source is None:
                            # 唯一容許的無來源格，是辨識器補入的右上角操作欄標題。
                            if rowIndex == 1 and colIndex == result.columns and node.text == _("操作（補入的欄名）"):
                                continue
                            raise MappingFailure("NO_SOURCE")
                        info = self.resolveCell(node, candidate.rows[rowIndex - 1][colIndex - 1])
                        start, end = info._startOffset, info._endOffset
                        if start < previousEnd or start >= end:
                            raise MappingFailure("RANGE_ORDER")
                        # 已存在原生表格欄位時不再套用，避免表格巢狀衝突。
                        for command in info.getTextWithFields():
                            if isinstance(command, textInfos.FieldCommand) and command.command == "controlStart" and command.field.get("table-id") is not None:
                                raise MappingFailure("NATIVE_TABLE")
                        result.cells[rowIndex, colIndex] = Cell(start, end, rowIndex, colIndex)
                        previousEnd = end
                cells = list(result.cells.values())
                if not cells:
                    raise MappingFailure("NO_CELLS")
                result.start, result.end = cells[0].start, cells[-1].end
                if any(result.start < other.end and result.end > other.start for other in mapped):
                    raise MappingFailure("TABLE_OVERLAP")
                result.snapshot = self.baseInfo(self.document, Offsets(result.start, result.end)).text
                mapped.append(result)
            except Exception as error:
                # 任何一格不可靠時整個候選跳過，絕不移到猜測的位置。
                code = str(error) if isinstance(error, MappingFailure) else "READ_ERROR"
                if code in {"NO_RANGE", "EMPTY_RANGE", "TEXT_MISMATCH", "CHILD_NO_RANGE", "CHILD_RANGE_ORDER", "CHILD_TEXT_MISMATCH", "CHILD_COMBINED_MISMATCH"}:
                    try:
                        result = self.mapSequence(candidate, result)
                        if any(result.start < other.end and result.end > other.start for other in mapped):
                            raise MappingFailure("TABLE_OVERLAP")
                        mapped.append(result)
                        self.sequenceMatches += 1
                        continue
                    except Exception as sequenceError:
                        code = str(sequenceError) if isinstance(sequenceError, MappingFailure) else "READ_ERROR"
                        rowIndex = colIndex = 0
                self.diagnostics.append((candidateIndex, rowIndex, colIndex, code))
                continue
        self.tables = sorted(mapped, key=lambda table: table.start)
        return len(self.tables)

    def valid(self):
        if not self.active:
            return False
        try:
            if self.restricted() or self.document.VBufHandle != self.handle:
                raise LookupError
            for table in self.tables:
                if self.baseInfo(self.document, Offsets(table.start, table.end)).text != table.snapshot:
                    raise LookupError
            return True
        except Exception:
            self.detach()
            return False

    def patch(self, name, value):
        # 記住原本是否有實例屬性；撤除時不遺留不必要的覆寫。
        self.saved[name] = (name in self.document.__dict__, self.document.__dict__.get(name))
        self.installed[name] = value
        setattr(self.document, name, value)

    def install(self):
        if not self.tables:
            return
        owner = self
        originalIter = self.document._iterNodesByType
        originalAt = self.document._getTableCellAt
        originalNear = self.document._getNearestTableCell
        originalUpdate = self.document._handleUpdate
        originalUnload = self.document.unloadBuffer
        originalInfo = self.baseInfo

        class TableTextInfo(originalInfo):
            def _getLineOffsets(info, offset):
                return owner.readingBounds(offset, originalInfo._getLineOffsets(info, offset))

            def _getParagraphOffsets(info, offset):
                return owner.readingBounds(offset, originalInfo._getParagraphOffsets(info, offset))

            def _setSelectionOffsets(info, start, end):
                result = originalInfo._setSelectionOffsets(info, start, end)
                if owner.valid():
                    # 先寫入真正的網頁游標，再讓點字重新取得相同位置。
                    import braille
                    braille.handler.handleCaretMove(owner.document, shouldAutoTether=False)
                return result

            def getTextWithFields(info, formatConfig=None):
                if not owner.valid():
                    return originalInfo.getTextWithFields(info, formatConfig)
                return owner.fields(info, formatConfig)

        def iterate(document, nodeType, direction="next", pos=None):
            native = originalIter(nodeType, direction, pos)
            if nodeType != "table" or direction not in {"next", "previous"} or not owner.valid():
                return native
            offset = pos._startOffset if pos is not None else (-1 if direction == "next" else float("inf"))
            tables = owner.tables if direction == "next" else reversed(owner.tables)
            custom = (browseMode.TextInfoQuickNavItem("table", document, document.makeTextInfo(Offsets(table.start, table.end))) for table in tables if (table.start > offset if direction == "next" else table.start < offset))
            return browseMode.mergeQuickNavItemIterators((native, custom), direction)

        def at(document, tableID, startPos, row, column):
            if owner.valid():
                for table in owner.tables:
                    if table.identifier == tableID:
                        cell = table.cells.get((row, column))
                        if cell is None:
                            raise LookupError
                        return document.makeTextInfo(Offsets(cell.start, cell.end))
            return originalAt(tableID, startPos, row, column)

        def nearest(document, startPos, cell, movement, axis):
            if owner.valid() and any(table.identifier == cell.tableID for table in owner.tables):
                # 重用 NVDA 本身的行列移動、缺格與邊界演算法。
                return documentBase.DocumentWithTableNavigation._getNearestTableCell(document, startPos, cell, movement, axis)
            return originalNear(startPos, cell, movement, axis)

        def update(document):
            # 更新通知不代表表格已改變；焦點或其他區域更新也可能觸發。
            # 核對原位置與文字後保留有效對應，失效時由 valid 撤除。
            owner.valid()
            return originalUpdate()

        def unload(document):
            owner.detach()
            return originalUnload()

        try:
            self.patch("TextInfo", TableTextInfo)
            for name, function in (("_iterNodesByType", iterate), ("_getTableCellAt", at), ("_getNearestTableCell", nearest), ("_handleUpdate", update), ("unloadBuffer", unload)):
                self.patch(name, MethodType(function, self.document))
            self.document._lastTableSelection = None
            self.active = True
        except Exception:
            self.detach()
            raise

    def detach(self):
        wasActive = self.active
        self.active = False
        for name, replacement in self.installed.items():
            # 不覆蓋另一個附加元件後來自行替換的屬性。
            if self.document.__dict__.get(name) is not replacement:
                continue
            existed, previous = self.saved[name]
            if existed:
                setattr(self.document, name, previous)
            else:
                delattr(self.document, name)
        self.installed.clear()
        self.saved.clear()
        self.tables.clear()
        if wasActive:
            self.document._lastTableSelection = None

    def readingBounds(self, offset, bounds):
        # 保留原有換行，但不能把相鄰儲存格併入同一個閱讀單位。
        # 格間空白也切開，讓向前及向後翻行都能持續移動。
        if not self.valid():
            return bounds
        left, right = bounds
        for table in self.tables:
            for cell in table.cells.values():
                for boundary in (cell.start, cell.end):
                    if left < boundary <= offset:
                        left = boundary
                    elif offset < boundary < right:
                        right = boundary
        return left, right

    def fields(self, info, formatConfig):
        start, end = info._startOffset, info._endOffset
        if start >= end:
            return []
        boundaries = {start, end}
        for table in self.tables:
            for cell in table.cells.values():
                if start < cell.start < end:
                    boundaries.add(cell.start)
                if start < cell.end < end:
                    boundaries.add(cell.end)
        boundaries = sorted(boundaries)
        output = []
        for left, right in zip(boundaries, boundaries[1:]):
            original = self.baseInfo(self.document, Offsets(left, right)).getTextWithFields(formatConfig)
            match = next(((table, cell) for table in self.tables for cell in table.cells.values() if cell.start <= left < cell.end), None)
            if match is None:
                output.extend(original)
                continue
            # 只移除推測儲存格中的「可點擊」提示，不改網站操作、角色或其他狀態。
            # 複製欄位與狀態集合，避免改到 NVDA 原始資料及表格外的內容。
            cleaned = []
            for command in original:
                if (isinstance(command, textInfos.FieldCommand)
                        and command.command in {"controlStart", "controlEnd"}
                        and command.field
                        and State.CLICKABLE in (command.field.get("states") or ())):
                    control = textInfos.ControlField(command.field)
                    control["states"] = set(control["states"]) - {State.CLICKABLE}
                    command = textInfos.FieldCommand(command.command, control)
                cleaned.append(command)
            original = cleaned
            table, cell = match
            tableField = textInfos.ControlField({"role": Role.TABLE, "table-id": table.identifier, "table-rowcount": table.rows, "table-columncount": table.columns})
            cellField = textInfos.ControlField({"role": Role.TABLECOLUMNHEADER if table.header and cell.row == 1 else Role.TABLECELL, "table-id": table.identifier, "table-rownumber": cell.row, "table-columnnumber": cell.col, "table-rowsspanned": 1, "table-columnsspanned": 1})
            if table.header and cell.row > 1:
                cellField["table-columnheadertext"] = table.names[cell.col - 1]
            output.extend([textInfos.FieldCommand("controlStart", tableField), textInfos.FieldCommand("controlStart", cellField)])
            output.extend(original)
            output.extend([textInfos.FieldCommand("controlEnd", cellField), textInfos.FieldCommand("controlEnd", tableField)])
        return output
