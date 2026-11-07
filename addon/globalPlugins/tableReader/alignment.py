"""核對整張表格唯一的完整文字序列；傳回 UTF-16 位置，不執行逐格模糊搜尋。"""
import addonHandler

addonHandler.initTranslation()


class AlignmentFailure(Exception):
    """只使用固定代碼，不包含任何來源文字。"""


def align(documentText, cells):
    # NVDA 虛擬緩衝區以 UTF-16 計算位置；emoji 等字元會占兩個位置。
    if len(documentText) > 500000:
        raise AlignmentFailure("SEQUENCE_LIMIT")
    compact, positions = [], []
    offset = 0
    for character in documentText:
        size = 2 if ord(character) > 0xFFFF else 1
        if not character.isspace():
            compact.append(character)
            positions.append((offset, offset + size))
        offset += size
    tokens = [''.join(ch for ch in value if not ch.isspace()) for value in cells]
    if sum(bool(token) for token in tokens) < 4:
        raise AlignmentFailure("SEQUENCE_INSUFFICIENT")
    wanted = ''.join(tokens)
    available = ''.join(compact)
    start = available.find(wanted)
    if start < 0:
        raise AlignmentFailure("SEQUENCE_NOT_FOUND")
    if available.find(wanted, start + 1) >= 0:
        raise AlignmentFailure("SEQUENCE_AMBIGUOUS")
    ranges = []
    cursor = start
    for token in tokens:
        # 空白格沒有可靠文字位置；保留格子順序，但不虛構範圍。
        if not token:
            ranges.append(None)
            continue
        ranges.append((positions[cursor][0], positions[cursor + len(token) - 1][1]))
        cursor += len(token)
    return ranges
