"""只處理記憶體中的資料；不存檔、不連線，也不猜測文字中的數字欄位。"""
from dataclasses import dataclass, field
from html import escape
import addonHandler

addonHandler.initTranslation()


@dataclass
class Node:
    role: str = "UNKNOWN"
    text: str = ""
    rect: tuple | None = None
    rowSpan: int = 1
    columnSpan: int = 1
    blocked: bool = False
    children: list = field(default_factory=list)
    source: object = field(default=None, repr=False)


@dataclass
class Table:
    rows: list
    header: bool
    inferred: bool
    nodes: list = field(default_factory=list, repr=False)


CELL_ROLES = {"TABLECELL", "TABLECOLUMNHEADER", "TABLEROWHEADER"}


def content(node):
    # 有子物件時只讀子物件，避免父物件名稱重複包含整列文字。
    if node.blocked:
        return ""
    if node.children:
        return " ".join(filter(None, (content(child) for child in node.children)))
    return node.text


def walk(node):
    yield node
    for child in node.children:
        yield from walk(child)


def collectRoles(node, roles):
    # 找到一個儲存格或列後停止向下找，避免重複收錄巢狀結構。
    for child in node.children:
        if child.role in roles:
            yield child
        elif child.role != "TABLE":
            yield from collectRoles(child, roles)


def unwrap(node):
    # 網頁常在每列外面多包一層無意義容器。
    for unused in range(4):
        if len(node.children) != 1 or node.role in CELL_ROLES:
            break
        node = node.children[0]
    return node


def findTables(root):
    tables, used = [], set()

    def add(rows, inferred):
        if len(rows) < (2 if inferred else 1) or not 2 <= len(rows[0]) <= 40:
            return
        if any(len(row) != len(rows[0]) for row in rows):
            return
        cells = [cell for row in rows for cell in row]
        if any(id(cell) in used for cell in cells):
            return
        # 遇到隱藏、密碼、編輯欄位或合併格，整個候選略過，避免資料移位。
        if any(part.blocked or part.rowSpan != 1 or part.columnSpan != 1
               for cell in cells for part in walk(cell)):
            return
        values = [[content(cell) for cell in row] for row in rows]
        if not any(value for row in values for value in row):
            return
        tables.append(Table(values, all(cell.role == "TABLECOLUMNHEADER" for cell in rows[0]), inferred, rows))
        # 將子物件也標記，以免同一資料又被辨識成另一個排列式表格。
        used.update(id(part) for cell in cells for part in walk(cell))

    nodes = list(walk(root))
    for node in nodes:
        if node.role == "TABLE" or any(child.role == "TABLEROW" for child in node.children):
            rows = [list(collectRoles(row, CELL_ROLES)) for row in collectRoles(node, {"TABLEROW"})]
            if rows:
                add(rows, False)
    for node in nodes:
        if len(tables) >= 30:
            break
        if node.role in CELL_ROLES or node.role in {"TABLE", "TABLEROW"} or len(node.children) < 2:
            continue
        rows = [unwrap(child).children for child in node.children]
        trailingAction = False
        # 有些網站的標題列少了最後一個「操作」欄，但每筆資料都有選單按鈕。
        # 必須至少有兩筆資料，而且每列最後一格確實暴露按鈕角色才補占位格。
        if len(rows) >= 3 and len(rows[0]) >= 2 and all(len(row) == len(rows[0]) + 1 for row in rows[1:]):
            buttonRoles = {"BUTTON", "MENUBUTTON", "DROPDOWNBUTTON", "SPLITBUTTON"}
            if all(any(part.role in buttonRoles for part in walk(row[-1])) for row in rows[1:]):
                reference = rows[1][-1].rect
                first = rows[0][0].rect
                if reference and first:
                    placeholder = Node(role="TABLECOLUMNHEADER", text=_("操作（補入的欄名）"), rect=(reference[0], first[1], reference[2], first[3]))
                    rows = [rows[0] + [placeholder]] + rows[1:]
                    trailingAction = True
        if not rows or not 2 <= len(rows[0]) <= 40 or any(len(row) != len(rows[0]) for row in rows):
            continue
        if any(not cell.rect or cell.rect[2] <= 0 or cell.rect[3] <= 0 for row in rows for cell in row):
            continue
        aligned = True
        for rowIndex, row in enumerate(rows):
            for colIndex, cell in enumerate(row):
                x, y, w, h = cell.rect
                reference = rows[1 if trailingAction else 0][colIndex].rect
                if trailingAction and rowIndex == 0:
                    # 標題可能置中，資料可能靠左；仍須落在同欄範圍內。
                    if min(x + w, reference[0] + reference[2]) <= max(x, reference[0]):
                        aligned = False
                elif abs(x - reference[0]) > 5:
                    aligned = False
                if colIndex:
                    px, py, pw, ph = row[colIndex - 1].rect
                    if x < px + pw - 3 or min(y + h, py + ph) <= max(y, py):
                        aligned = False
            if rowIndex and row[0].rect[1] < rows[rowIndex - 1][0].rect[1] + rows[rowIndex - 1][0].rect[3] - 3:
                aligned = False
        if aligned:
            add(rows, True)
    return tables


def render(tables, firstRowHeader=False):
    # 只建立我們自己的標籤；來源文字一律轉義，不能變成可執行的 HTML。
    parts = ["<h1>" + escape(_("通用表格閱讀器")) + "</h1>",
             "<p>" + escape(_("這是目前已載入資料的副本，可能不是完整表格。按 T 找表格，使用 Ctrl＋Alt＋方向鍵逐格閱讀。關閉後返回原網頁。")) + "</p>",
             "<p>" + escape(_("來源按鈕與連結只呈現文字；操作請回原網頁。推測表格可能是卡片排列，請核對欄位。")) + "</p>"]
    for index, table in enumerate(tables, 1):
        heading = _("表格 {number}：{kind}").format(number=index, kind=_("依位置推測") if table.inferred else _("依無障礙結構辨識"))
        parts.append("<h2>" + escape(heading) + "</h2><table><caption>" + escape(heading) + "</caption>")
        hasHeader = table.header or firstRowHeader
        names = table.rows[0] if hasHeader else [_("第 {number} 欄").format(number=i + 1) for i in range(len(table.rows[0]))]
        parts.append("<thead><tr>" + "".join('<th scope="col">' + escape(name) + "</th>" for name in names) + "</tr></thead><tbody>")
        for row in table.rows[1 if hasHeader else 0:]:
            parts.append("<tr>" + "".join("<td>" + escape(value) + "</td>" for value in row) + "</tr>")
        parts.append("</tbody></table>")
    return "".join(parts)
