"""
qr.py — self-contained QR Code encoder (byte mode, error correction level M).

Why not the `qrcode` package?  The Pi prints the table QR codes itself and the
venue may have no internet access.  This module has **no dependencies**: it
returns a boolean matrix that `to_png_bytes()` renders with Pillow (already a
requirement of this project for the map image).

Supported subset (enough for table / order QR codes):
  * byte mode (UTF-8 payload)
  * error correction level M
  * versions 1 .. 10  (up to 213 bytes of payload)
  * all 8 data masks, chosen by the standard penalty score

The implementation follows ISO/IEC 18004.  `tests/test_qr.py` checks the
structure of the result and (when the optional `qrcode` package is installed)
compares the matrix against that reference implementation.
"""

from __future__ import annotations

import io
from typing import List, Optional

# ── Galois field GF(256), primitive polynomial 0x11D ─────────────────────────

_EXP = [0] * 512
_LOG = [0] * 256


def _init_tables() -> None:
    x = 1
    for i in range(255):
        _EXP[i] = x
        _LOG[x] = i
        x <<= 1
        if x & 0x100:
            x ^= 0x11D
    for i in range(255, 512):
        _EXP[i] = _EXP[i - 255]


_init_tables()


def _gf_mul(a: int, b: int) -> int:
    if a == 0 or b == 0:
        return 0
    return _EXP[_LOG[a] + _LOG[b]]


def _poly_mul(a: List[int], b: List[int]) -> List[int]:
    res = [0] * (len(a) + len(b) - 1)
    for i, av in enumerate(a):
        if av == 0:
            continue
        for j, bv in enumerate(b):
            res[i + j] ^= _gf_mul(av, bv)
    return res


def _rs_generator(degree: int) -> List[int]:
    """Generator polynomial for `degree` error correction codewords."""
    g = [1]
    for i in range(degree):
        g = _poly_mul(g, [1, _EXP[i]])
    return g


def _rs_encode(data: List[int], ec_len: int) -> List[int]:
    """Reed-Solomon remainder of `data` (the EC codewords)."""
    gen = _rs_generator(ec_len)
    buf = list(data) + [0] * ec_len
    for i in range(len(data)):
        factor = buf[i]
        if factor == 0:
            continue
        for j in range(1, len(gen)):
            buf[i + j] ^= _gf_mul(gen[j], factor)
    return buf[len(data):]


# ── Version tables (error correction level M) ─────────────────────────────────
# (data_codewords, ec_codewords_per_block, [block_data_codewords, ...])

_LEVEL_M: dict[int, tuple[int, int, List[int]]] = {
    1: (16, 10, [16]),
    2: (28, 16, [28]),
    3: (44, 26, [44]),
    4: (64, 18, [32, 32]),
    5: (86, 24, [43, 43]),
    6: (108, 16, [27, 27, 27, 27]),
    7: (124, 18, [31, 31, 31, 31]),
    8: (154, 22, [38, 38, 39, 39]),
    9: (182, 22, [36, 36, 36, 37, 37]),
    10: (216, 26, [43, 43, 43, 43, 44]),
}

_ALIGNMENT_POSITIONS: dict[int, List[int]] = {
    1: [], 2: [6, 18], 3: [6, 22], 4: [6, 26], 5: [6, 30],
    6: [6, 34], 7: [6, 22, 38], 8: [6, 24, 42], 9: [6, 26, 46], 10: [6, 28, 50],
}

_ECC_FORMAT_BITS = 0b00       # level M
_MAX_VERSION = max(_LEVEL_M)


class QRError(ValueError):
    """Raised when the payload does not fit into the supported QR versions."""


# ── Bit stream helpers ────────────────────────────────────────────────────────

class _BitBuffer:
    def __init__(self) -> None:
        self.bits: List[int] = []

    def append(self, value: int, length: int) -> None:
        for i in range(length - 1, -1, -1):
            self.bits.append((value >> i) & 1)

    def __len__(self) -> int:
        return len(self.bits)


def _choose_version(byte_len: int) -> int:
    for version in range(1, _MAX_VERSION + 1):
        data_bits = _LEVEL_M[version][0] * 8
        needed = 4 + 8 + byte_len * 8        # mode + length + payload
        if needed <= data_bits:
            return version
    raise QRError(
        f"Payload too long for a QR code: {byte_len} bytes "
        f"(maximum is {_LEVEL_M[_MAX_VERSION][0] - 3} bytes)")


def _build_data_codewords(payload: bytes, version: int) -> List[int]:
    """Byte-mode data codewords, split into blocks, with EC codewords appended."""
    data_capacity = _LEVEL_M[version][0]
    ec_per_block = _LEVEL_M[version][1]
    blocks = _LEVEL_M[version][2]

    buf = _BitBuffer()
    buf.append(0b0100, 4)               # byte mode indicator
    buf.append(len(payload), 8)         # character count (versions 1-9 use 8 bits)
    for byte in payload:
        buf.append(byte, 8)

    # Terminator + byte alignment
    capacity_bits = data_capacity * 8
    if len(buf) < capacity_bits:
        buf.append(0, min(4, capacity_bits - len(buf)))
    if len(buf) % 8:
        buf.append(0, 8 - (len(buf) % 8))
    # Pad codewords 0xEC / 0x11
    pad = [0xEC, 0x11]
    i = 0
    while len(buf) < capacity_bits:
        buf.append(pad[i % 2], 8)
        i += 1

    raw = []
    for bit in buf.bits:
        raw.append(bit)
    data = []
    for i in range(0, len(raw), 8):
        byte = 0
        for bit in raw[i:i + 8]:
            byte = (byte << 1) | bit
        data.append(byte)

    # Split into blocks, compute EC per block, then interleave.
    block_sizes = blocks
    ec_total = ec_per_block * len(block_sizes)
    all_blocks: List[List[int]] = []
    pos = 0
    for size in block_sizes:
        all_blocks.append(data[pos:pos + size])
        pos += size
    for block in all_blocks:
        block.extend(_rs_encode(block, ec_per_block))

    # Interleave data codewords, then EC codewords.
    out: List[int] = []
    max_data = max(len(b) - ec_per_block for b in all_blocks)
    for i in range(max_data):
        for block in all_blocks:
            if i < len(block) - ec_per_block:
                out.append(block[i])
    for i in range(ec_per_block):
        for block in all_blocks:
            out.append(block[-ec_per_block + i])
    assert len(out) == data_capacity + ec_total
    return out


# ── Matrix construction ───────────────────────────────────────────────────────

def _new_matrix(size: int) -> tuple[List[List[int]], List[List[bool]]]:
    return ([[0] * size for _ in range(size)],
            [[False] * size for _ in range(size)])


def _place_finder(mods, reserved, row: int, col: int) -> None:
    size = len(mods)
    for r in range(-1, 8):
        for c in range(-1, 8):
            rr, cc = row + r, col + c
            if not (0 <= rr < size and 0 <= cc < size):
                continue
            dark = (0 <= r <= 6 and (c == 0 or c == 6)) or \
                   (0 <= c <= 6 and (r == 0 or r == 6)) or \
                   (2 <= r <= 4 and 2 <= c <= 4)
            mods[rr][cc] = 1 if dark else 0
            reserved[rr][cc] = True


def _place_alignment(mods, reserved, row: int, col: int) -> None:
    for dr in range(-2, 3):
        for dc in range(-2, 3):
            rr, cc = row + dr, col + dc
            mods[rr][cc] = 0 if max(abs(dr), abs(dc)) == 1 else 1
            reserved[rr][cc] = True


def _reserve_format(mods, reserved) -> None:
    size = len(mods)
    for i in range(9):
        reserved[8][i] = True
        reserved[i][8] = True
    for i in range(8):
        reserved[8][size - 1 - i] = True
        reserved[size - 1 - i][8] = True
    # Dark module
    mods[size - 8][8] = 1
    reserved[size - 8][8] = True


def _place_function_patterns(version: int) -> tuple[List[List[int]], List[List[bool]]]:
    size = version * 4 + 17
    mods, reserved = _new_matrix(size)

    _place_finder(mods, reserved, 0, 0)
    _place_finder(mods, reserved, 0, size - 7)
    _place_finder(mods, reserved, size - 7, 0)

    # Timing patterns
    for i in range(size):
        if not reserved[6][i]:
            mods[6][i] = 1 if i % 2 == 0 else 0
            reserved[6][i] = True
        if not reserved[i][6]:
            mods[i][6] = 1 if i % 2 == 0 else 0
            reserved[i][6] = True

    # Alignment patterns (skip the three corners)
    positions = _ALIGNMENT_POSITIONS[version]
    for r in positions:
        for c in positions:
            if (r <= 8 and c <= 8) or (r <= 8 and c >= size - 9) or (r >= size - 9 and c <= 8):
                continue
            _place_alignment(mods, reserved, r, c)

    _reserve_format(mods, reserved)

    # Version information (version 7 and above)
    if version >= 7:
        bits = _version_bits(version)
        for i in range(18):
            bit = (bits >> i) & 1
            r = i // 3
            c = size - 11 + (i % 3)
            mods[r][c] = bit
            mods[c][r] = bit
            reserved[r][c] = True
            reserved[c][r] = True

    return mods, reserved


def _place_data(mods, reserved, codewords: List[int]) -> None:
    size = len(mods)
    bits: List[int] = []
    for cw in codewords:
        for i in range(7, -1, -1):
            bits.append((cw >> i) & 1)
    index = 0

    for right in range(size - 1, 0, -2):
        if right <= 6:
            right -= 1
        for vert in range(size):
            upward = ((right + 1) & 2) == 0
            y = (size - 1 - vert) if upward else vert
            for j in range(2):
                x = right - j
                if reserved[y][x]:
                    continue
                mods[y][x] = bits[index] if index < len(bits) else 0
                index += 1


def _apply_mask(mods: List[List[int]], reserved: List[List[bool]], mask: int) -> None:
    size = len(mods)
    for y in range(size):
        for x in range(size):
            if reserved[y][x]:
                continue
            invert = False
            if mask == 0:
                invert = (y + x) % 2 == 0
            elif mask == 1:
                invert = y % 2 == 0
            elif mask == 2:
                invert = x % 3 == 0
            elif mask == 3:
                invert = (y + x) % 3 == 0
            elif mask == 4:
                invert = (y // 2 + x // 3) % 2 == 0
            elif mask == 5:
                invert = (y * x) % 2 + (y * x) % 3 == 0
            elif mask == 6:
                invert = ((y * x) % 2 + (y * x) % 3) % 2 == 0
            elif mask == 7:
                invert = ((y + x) % 2 + (y * x) % 3) % 2 == 0
            if invert:
                mods[y][x] ^= 1


def _format_bits(mask: int) -> int:
    data = (_ECC_FORMAT_BITS << 3) | mask
    rem = data
    for _ in range(10):
        rem = (rem << 1) ^ ((rem >> 9) * 0x537)
    return ((data << 10) | rem) ^ 0x5412


def _version_bits(version: int) -> int:
    rem = version
    for _ in range(12):
        rem = (rem << 1) ^ ((rem >> 11) * 0x1F25)
    return (version << 12) | rem


def _draw_format(mods: List[List[int]], mask: int) -> None:
    """Write both copies of the 15 bit format information.

    Copy 1 wraps around the top-left finder (column 8 and row 8),
    copy 2 sits between the top-right and bottom-left finders.
    """
    size = len(mods)
    bits = _format_bits(mask)
    for i in range(15):
        bit = (bits >> i) & 1
        # Copy 1 — column 8 (rows 0-5, 7, 8) then row 8 (cols 5-0)
        if i < 6:
            mods[i][8] = bit
        elif i == 6:
            mods[7][8] = bit
        elif i == 7:
            mods[8][8] = bit
        elif i == 8:
            mods[8][7] = bit
        else:
            mods[8][14 - i] = bit
        # Copy 2 — row 8 (right side) then column 8 (bottom side)
        if i < 8:
            mods[8][size - 1 - i] = bit
        else:
            mods[size - 15 + i][8] = bit


def _penalty(mods: List[List[int]]) -> int:
    size = len(mods)
    score = 0

    # Rule 1: runs of 5+ identical modules in a row / column
    for line in list(mods) + [list(col) for col in zip(*mods)]:
        run_value = line[0]
        run_length = 1
        for i in range(1, size):
            if line[i] == run_value:
                run_length += 1
            else:
                if run_length >= 5:
                    score += 3 + (run_length - 5)
                run_value = line[i]
                run_length = 1
        if run_length >= 5:
            score += 3 + (run_length - 5)

    # Rule 2: 2x2 blocks of the same colour
    for y in range(size - 1):
        for x in range(size - 1):
            v = mods[y][x]
            if v == mods[y][x + 1] == mods[y + 1][x] == mods[y + 1][x + 1]:
                score += 3

    # Rule 3: finder-like 1:1:3:1:1 patterns with four light modules on a side
    pattern_a = [1, 0, 1, 1, 1, 0, 1, 0, 0, 0, 0]
    pattern_b = [0, 0, 0, 0, 1, 0, 1, 1, 1, 0, 1]
    for line in list(mods) + [list(col) for col in zip(*mods)]:
        for i in range(size - 10):
            window = line[i:i + 11]
            if window == pattern_a or window == pattern_b:
                score += 40

    # Rule 4: 10 points for every 5 % the dark ratio deviates from 50 %
    dark = sum(sum(row) for row in mods)
    total = size * size
    steps = (abs(dark * 20 - total * 10) + total - 1) // total - 1
    score += steps * 10
    return score


def encode(payload: str) -> List[List[int]]:
    """Encode `payload` (UTF-8 text) and return the QR matrix.

    Returns a list of rows; each row is a list of 0/1 where 1 means dark.
    """
    data = payload.encode("utf-8")
    version = _choose_version(len(data))
    codewords = _build_data_codewords(data, version)

    best: Optional[tuple[int, List[List[int]]]] = None
    for mask in range(8):
        mods, reserved = _place_function_patterns(version)
        _place_data(mods, reserved, codewords)
        _apply_mask(mods, reserved, mask)
        _draw_format(mods, mask)
        score = _penalty(mods)
        if best is None or score < best[0]:
            best = (score, mods)

    assert best is not None
    return best[1]


def to_png_bytes(payload: str, *, scale: int = 8, border: int = 4) -> bytes:
    """Render `payload` as a PNG (black on white) and return the raw bytes."""
    from PIL import Image

    matrix = encode(payload)
    size = len(matrix)
    dim = (size + border * 2) * scale
    img = Image.new("L", (dim, dim), 255)
    pixels = img.load()
    for y, row in enumerate(matrix):
        for x, value in enumerate(row):
            if not value:
                continue
            for dy in range(scale):
                for dx in range(scale):
                    px = (x + border) * scale + dx
                    py = (y + border) * scale + dy
                    pixels[px, py] = 0
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()
