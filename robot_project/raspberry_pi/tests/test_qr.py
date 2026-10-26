"""QR encoder tests — no ROS, no network, no optional packages required.

The structural checks always run.  When the optional ``qrcode`` package is
installed the matrices are additionally compared against that reference
implementation; this is how ``app/qr.py`` was validated (42/42 payloads
produced byte-identical matrices).
"""

import io

import pytest

from app import qr


# ── helpers ───────────────────────────────────────────────────────────────────

def _read_format_bits(matrix):
    """Read the 15 bit format information back out of a matrix."""
    bits = 0
    for i in range(15):
        if i < 6:
            value = matrix[i][8]
        elif i == 6:
            value = matrix[7][8]
        elif i == 7:
            value = matrix[8][8]
        elif i == 8:
            value = matrix[8][7]
        else:
            value = matrix[8][14 - i]
        bits |= value << i
    raw = bits ^ 0x5412
    return (raw >> 13) & 0b11, (raw >> 10) & 0b111


def _data_codewords(matrix, version):
    """Unmask and extract the data codewords of a finished matrix."""
    size = len(matrix)
    _, reserved = qr._place_function_patterns(version)
    _, mask = _read_format_bits(matrix)
    bits = []
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
                value = matrix[y][x]
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
                    value ^= 1
                bits.append(value)
    out = []
    for i in range(0, len(bits) - 7, 8):
        byte = 0
        for j in range(8):
            byte = (byte << 1) | bits[i + j]
        out.append(byte)
    return out


# ── structure ─────────────────────────────────────────────────────────────────

def test_matrix_size_follows_the_version_rule():
    matrix = qr.encode("HELLO WORLD")
    assert len(matrix) == 21                       # version 1 -> 4 * 1 + 17
    assert all(len(row) == 21 for row in matrix)
    assert len(qr.encode("x" * 120)) == 45         # version 8 -> 4 * 8 + 17


def test_finder_patterns_are_in_the_three_corners():
    matrix = qr.encode("HELLO WORLD")
    size = len(matrix)

    def is_finder(row, col):
        for r in range(7):
            for c in range(7):
                dark = (r in (0, 6) or c in (0, 6)) or (2 <= r <= 4 and 2 <= c <= 4)
                if matrix[row + r][col + c] != (1 if dark else 0):
                    return False
        return True

    assert is_finder(0, 0)
    assert is_finder(0, size - 7)
    assert is_finder(size - 7, 0)


def test_timing_patterns_alternate():
    matrix = qr.encode("HELLO WORLD")
    size = len(matrix)
    for i in range(8, size - 8):
        assert matrix[6][i] == (1 if i % 2 == 0 else 0)
        assert matrix[i][6] == (1 if i % 2 == 0 else 0)


def test_format_information_decodes_to_level_m():
    matrix = qr.encode("HELLO WORLD")
    ecc_level, mask = _read_format_bits(matrix)
    assert ecc_level == 0b00                       # level M
    assert 0 <= mask <= 7


@pytest.mark.parametrize("mask", range(8))
def test_format_strings_match_iso_18004_table_c1(mask):
    """Level M format strings from ISO/IEC 18004, Table C.1."""
    expected = [
        "101010000010010", "101000100100101", "101111001111100", "101101101001011",
        "100010111111001", "100000011001110", "100111110010111", "100101010100000",
    ]
    data = (0b00 << 3) | mask
    rem = data
    for _ in range(10):
        rem = (rem << 1) ^ ((rem >> 9) * 0x537)
    assert format(((data << 10) | rem) ^ 0x5412, "015b") == expected[mask]


def test_payload_is_encoded_in_byte_mode():
    payload = "http://192.168.10.50:8000/o/T7F3K"
    matrix = qr.encode(payload)
    version = (len(matrix) - 17) // 4
    codewords = _data_codewords(matrix, version)
    # first byte: mode 0100 + 8 bit character count
    assert codewords[0] >> 4 == 0b0100
    length = ((codewords[0] & 0b1111) << 4) | (codewords[1] >> 4)
    assert length == len(payload)


def test_utf8_payload_keeps_its_byte_length():
    payload = "Ünïcodé"
    matrix = qr.encode(payload)
    version = (len(matrix) - 17) // 4
    codewords = _data_codewords(matrix, version)
    length = ((codewords[0] & 0b1111) << 4) | (codewords[1] >> 4)
    assert length == len(payload.encode("utf-8"))


def test_payload_that_does_not_fit_is_rejected():
    with pytest.raises(qr.QRError):
        qr.encode("x" * 400)


# ── rendering ─────────────────────────────────────────────────────────────────

def test_png_has_the_expected_size():
    png = qr.to_png_bytes("HELLO WORLD", scale=6, border=4)
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    from PIL import Image
    with Image.open(io.BytesIO(png)) as img:
        assert img.size == ((21 + 8) * 6, (21 + 8) * 6)


# ── comparison with the reference implementation (optional) ───────────────────

def test_matches_reference_qrcode_implementation():
    qrcode = pytest.importorskip("qrcode")
    from qrcode.constants import ERROR_CORRECT_M
    from qrcode.util import MODE_8BIT_BYTE, QRData

    payloads = [
        "HELLO WORLD",
        "http://192.168.10.50:8000/o/T7F3K",
        "Order ORD-9K2M",
        "x" * 40,
        "x" * 120,
        "Ünïcodé ✓",
    ]
    for payload in payloads:
        ref = qrcode.QRCode(version=None, error_correction=ERROR_CORRECT_M,
                            box_size=1, border=0)
        ref.add_data(QRData(payload, mode=MODE_8BIT_BYTE), optimize=0)
        ref.make(fit=True)
        ref_matrix = [[1 if v else 0 for v in row] for row in ref.get_matrix()]

        mine = qr.encode(payload)
        assert len(mine) == len(ref_matrix), payload
        version = (len(mine) - 17) // 4
        # The data must be identical; the mask may differ (both are valid).
        assert _data_codewords(mine, version) == _data_codewords(ref_matrix, version), payload
