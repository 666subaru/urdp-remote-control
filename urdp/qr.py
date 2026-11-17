"""A small QR code encoder, enough for the tablet screen's address.

Byte mode, error correction level M, versions 1-10 (up to 213 bytes).  The
address is short, so a full library would be a dependency for nothing.
"""

from __future__ import annotations

# Level M, per version: (EC codewords per block, [(blocks, data codewords)]).
_BLOCKS: dict[int, tuple[int, list[tuple[int, int]]]] = {
    1: (10, [(1, 16)]),
    2: (16, [(1, 28)]),
    3: (26, [(1, 44)]),
    4: (18, [(2, 32)]),
    5: (24, [(2, 43)]),
    6: (16, [(4, 27)]),
    7: (18, [(4, 31)]),
    8: (22, [(2, 38), (2, 39)]),
    9: (22, [(3, 36), (2, 37)]),
    10: (26, [(4, 43), (1, 44)]),
}

_ALIGNMENT: dict[int, list[int]] = {
    1: [], 2: [6, 18], 3: [6, 22], 4: [6, 26], 5: [6, 30], 6: [6, 34],
    7: [6, 22, 38], 8: [6, 24, 42], 9: [6, 26, 46], 10: [6, 28, 50],
}

_FORMAT_LEVEL_M = 0


def _gf_tables() -> tuple[list[int], list[int]]:
    exp, log = [0] * 512, [0] * 256
    value = 1
    for i in range(255):
        exp[i] = value
        log[value] = i
        value <<= 1
        if value & 0x100:
            value ^= 0x11D
    for i in range(255, 512):
        exp[i] = exp[i - 255]
    return exp, log


_EXP, _LOG = _gf_tables()


def _mul(a: int, b: int) -> int:
    return 0 if a == 0 or b == 0 else _EXP[_LOG[a] + _LOG[b]]


def _ec_codewords(data: list[int], count: int) -> list[int]:
    generator = [1]
    for i in range(count):
        nxt = [0] * (len(generator) + 1)
        for j, coef in enumerate(generator):
            nxt[j] ^= coef
            nxt[j + 1] ^= _mul(coef, _EXP[i])
        generator = nxt
    remainder = [0] * count
    for byte in data:
        factor = byte ^ remainder[0]
        remainder = remainder[1:] + [0]
        for j in range(count):
            remainder[j] ^= _mul(generator[j + 1], factor)
    return remainder


def _codewords(payload: bytes, version: int) -> list[int]:
    ec_count, groups = _BLOCKS[version]
    capacity = sum(n * size for n, size in groups)
    bits: list[int] = []

    def push(value: int, width: int) -> None:
        bits.extend((value >> i) & 1 for i in range(width - 1, -1, -1))

    push(0b0100, 4)
    push(len(payload), 8 if version < 10 else 16)
    for byte in payload:
        push(byte, 8)
    push(0, min(4, capacity * 8 - len(bits)))
    push(0, -len(bits) % 8)
    data = [int("".join(map(str, bits[i:i + 8])), 2)
            for i in range(0, len(bits), 8)]
    pad = 0xEC
    while len(data) < capacity:
        data.append(pad)
        pad ^= 0xEC ^ 0x11

    blocks, offset = [], 0
    for n, size in groups:
        for _ in range(n):
            blocks.append(data[offset:offset + size])
            offset += size
    ecs = [_ec_codewords(block, ec_count) for block in blocks]
    out = []
    for i in range(max(len(b) for b in blocks)):
        out.extend(b[i] for b in blocks if i < len(b))
    for i in range(ec_count):
        out.extend(e[i] for e in ecs)
    return out


class _Grid:
    def __init__(self, version: int) -> None:
        self.size = version * 4 + 17
        self.dark = [[False] * self.size for _ in range(self.size)]
        self.fixed = [[False] * self.size for _ in range(self.size)]

    def set(self, x: int, y: int, dark: bool) -> None:
        self.dark[y][x] = dark
        self.fixed[y][x] = True


def _function_patterns(grid: _Grid, version: int) -> None:
    size = grid.size
    for i in range(size):
        grid.set(6, i, i % 2 == 0)
        grid.set(i, 6, i % 2 == 0)
    for cx, cy in ((3, 3), (size - 4, 3), (3, size - 4)):
        for dy in range(-4, 5):
            for dx in range(-4, 5):
                x, y = cx + dx, cy + dy
                if 0 <= x < size and 0 <= y < size:
                    ring = max(abs(dx), abs(dy))
                    grid.set(x, y, ring not in (2, 4))
    positions = _ALIGNMENT[version]
    last = len(positions) - 1
    for i, cx in enumerate(positions):
        for j, cy in enumerate(positions):
            if (i, j) in ((0, 0), (0, last), (last, 0)):
                continue
            for dy in range(-2, 3):
                for dx in range(-2, 3):
                    grid.set(cx + dx, cy + dy, max(abs(dx), abs(dy)) != 1)
    _format_bits(grid, 0)          # reserve; drawn for real after masking
    if version >= 7:
        rem = version
        for _ in range(12):
            rem = (rem << 1) ^ ((rem >> 11) * 0x1F25)
        bits = version << 12 | rem
        for i in range(18):
            bit = (bits >> i) & 1 == 1
            a, b = size - 11 + i % 3, i // 3
            grid.set(a, b, bit)
            grid.set(b, a, bit)


def _format_bits(grid: _Grid, mask: int) -> None:
    data = _FORMAT_LEVEL_M << 3 | mask
    rem = data
    for _ in range(10):
        rem = (rem << 1) ^ ((rem >> 9) * 0x537)
    bits = (data << 10 | rem) ^ 0x5412
    size = grid.size

    def bit(i: int) -> bool:
        return (bits >> i) & 1 == 1

    for i in range(6):
        grid.set(8, i, bit(i))
    grid.set(8, 7, bit(6))
    grid.set(8, 8, bit(7))
    grid.set(7, 8, bit(8))
    for i in range(9, 15):
        grid.set(14 - i, 8, bit(i))
    for i in range(8):
        grid.set(size - 1 - i, 8, bit(i))
    for i in range(8, 15):
        grid.set(8, size - 15 + i, bit(i))
    grid.set(8, size - 8, True)


def _place(grid: _Grid, codewords: list[int]) -> None:
    size = grid.size
    total = len(codewords) * 8
    index = 0
    right = size - 1
    while right >= 1:
        if right == 6:
            right = 5
        for vert in range(size):
            for j in range(2):
                x = right - j
                upward = ((right + 1) & 2) == 0
                y = size - 1 - vert if upward else vert
                if grid.fixed[y][x]:
                    continue
                if index < total:
                    grid.dark[y][x] = (codewords[index >> 3] >> (7 - (index & 7))) & 1 == 1
                    index += 1
        right -= 2


_MASKS = (
    lambda x, y: (x + y) % 2 == 0,
    lambda x, y: y % 2 == 0,
    lambda x, y: x % 3 == 0,
    lambda x, y: (x + y) % 3 == 0,
    lambda x, y: (x // 3 + y // 2) % 2 == 0,
    lambda x, y: x * y % 2 + x * y % 3 == 0,
    lambda x, y: (x * y % 2 + x * y % 3) % 2 == 0,
    lambda x, y: ((x + y) % 2 + x * y % 3) % 2 == 0,
)


def _apply_mask(grid: _Grid, mask: int) -> None:
    test = _MASKS[mask]
    for y in range(grid.size):
        for x in range(grid.size):
            if not grid.fixed[y][x] and test(x, y):
                grid.dark[y][x] = not grid.dark[y][x]


def _penalty(dark: list[list[bool]]) -> int:
    size = len(dark)
    score = 0
    lines = dark + [list(col) for col in zip(*dark)]
    finder = ([True, False, True, True, True, False, True, False, False, False, False],
              [False, False, False, False, True, False, True, True, True, False, True])
    for line in lines:
        run, prev = 0, None
        for value in line:
            if value == prev:
                run += 1
            else:
                if run >= 5:
                    score += run - 2
                run, prev = 1, value
        if run >= 5:
            score += run - 2
        for i in range(size - 10):
            window = line[i:i + 11]
            if window in finder:
                score += 40
    for y in range(size - 1):
        for x in range(size - 1):
            if dark[y][x] == dark[y][x + 1] == dark[y + 1][x] == dark[y + 1][x + 1]:
                score += 3
    total = size * size
    count = sum(map(sum, dark))
    k = (abs(count * 20 - total * 10) + total - 1) // total - 1
    return score + 10 * max(k, 0)


def encode(text: str) -> list[list[bool]]:
    """The modules of a QR code for *text*, ``[row][column]``, True = dark.

    No quiet zone is included.  Raises ValueError when the text is too long.
    """
    payload = text.encode("utf-8")
    for version in range(1, 11):
        _, groups = _BLOCKS[version]
        capacity = sum(n * size for n, size in groups)
        header = 4 + (8 if version < 10 else 16)
        if header + 8 * len(payload) <= capacity * 8:
            break
    else:
        raise ValueError("text too long for a QR code")

    codewords = _codewords(payload, version)
    best: tuple[int, list[list[bool]]] | None = None
    for mask in range(8):
        grid = _Grid(version)
        _function_patterns(grid, version)
        _place(grid, codewords)
        _apply_mask(grid, mask)
        _format_bits(grid, mask)
        score = _penalty(grid.dark)
        if best is None or score < best[0]:
            best = (score, grid.dark)
    assert best is not None
    return best[1]
