"""A small QR code encoder: byte mode, error correction level M, versions 1 to 10.

Share links are QR-coded on our own server so no third-party service ever sees
who is sharing what. Standard library only. The algorithm follows ISO/IEC 18004
(the structure mirrors Project Nayuki's public-domain reference): encode the
bytes, add Reed-Solomon error correction, place the modules, then pick the mask
with the lowest penalty.
"""
from html import escape
import struct
import zlib

# Level M, indexed by version (index 0 unused).
_ECC_PER_BLOCK = (None, 10, 16, 26, 18, 24, 16, 18, 22, 22, 26)
_BLOCKS = (None, 1, 1, 1, 2, 2, 4, 4, 4, 5, 5)
_FORMAT_LEVEL_M = 0
MAX_VERSION = 10


def _raw_modules(version):
    result = (16 * version + 128) * version + 64
    if version >= 2:
        count = version // 7 + 2
        result -= (25 * count - 10) * count - 55
        if version >= 7:
            result -= 36
    return result


def _data_capacity(version):
    return _raw_modules(version) // 8 - _ECC_PER_BLOCK[version] * _BLOCKS[version]


def _gf_multiply(x, y):
    z = 0
    for i in reversed(range(8)):
        z = (z << 1) ^ ((z >> 7) * 0x11D)
        z ^= ((y >> i) & 1) * x
    return z


def _rs_divisor(degree):
    result = [0] * (degree - 1) + [1]
    root = 1
    for _ in range(degree):
        for j in range(degree):
            result[j] = _gf_multiply(result[j], root)
            if j + 1 < degree:
                result[j] ^= result[j + 1]
        root = _gf_multiply(root, 0x02)
    return result


def _rs_remainder(data, divisor):
    result = [0] * len(divisor)
    for byte in data:
        factor = byte ^ result.pop(0)
        result.append(0)
        for i, coefficient in enumerate(divisor):
            result[i] ^= _gf_multiply(coefficient, factor)
    return result


def _codewords(payload, version):
    bits = []

    def put(value, length):
        bits.extend((value >> i) & 1 for i in reversed(range(length)))

    put(0b0100, 4)  # byte mode
    put(len(payload), 8 if version <= 9 else 16)
    for byte in payload:
        put(byte, 8)
    capacity = _data_capacity(version) * 8
    put(0, min(4, capacity - len(bits)))
    put(0, -len(bits) % 8)
    data = [int("".join(map(str, bits[i:i + 8])), 2) for i in range(0, len(bits), 8)]
    pad = 0xEC
    while len(data) < capacity // 8:
        data.append(pad)
        pad ^= 0xEC ^ 0x11
    # Split into blocks, add error correction, then interleave.
    blocks_count, ecc_length = _BLOCKS[version], _ECC_PER_BLOCK[version]
    raw = _raw_modules(version) // 8
    short_count = blocks_count - raw % blocks_count
    short_length = raw // blocks_count
    divisor = _rs_divisor(ecc_length)
    blocks, k = [], 0
    for i in range(blocks_count):
        size = short_length - ecc_length + (0 if i < short_count else 1)
        chunk = data[k:k + size]
        k += size
        ecc = _rs_remainder(chunk, divisor)
        if i < short_count:
            chunk = chunk + [0]
        blocks.append(chunk + ecc)
    result = []
    for i in range(len(blocks[0])):
        for j, block in enumerate(blocks):
            if i != short_length - ecc_length or j >= short_count:
                result.append(block[i])
    return result


class _Grid:
    def __init__(self, version):
        self.version = version
        self.size = version * 4 + 17
        self.dark = [[False] * self.size for _ in range(self.size)]
        self.function = [[False] * self.size for _ in range(self.size)]

    def set_function(self, x, y, dark):
        self.dark[y][x] = dark
        self.function[y][x] = True

    def alignment_positions(self):
        if self.version == 1:
            return []
        count = self.version // 7 + 2
        step = (self.version * 4 + count * 2 + 1) // (count * 2 - 2) * 2
        result, position = [6], self.size - 7
        while len(result) < count:
            result.insert(1, position)
            position -= step
        return result

    def draw_function_patterns(self):
        size = self.size
        for i in range(size):
            self.set_function(6, i, i % 2 == 0)
            self.set_function(i, 6, i % 2 == 0)
        for cx, cy in ((3, 3), (size - 4, 3), (3, size - 4)):
            for dy in range(-4, 5):
                for dx in range(-4, 5):
                    x, y = cx + dx, cy + dy
                    if 0 <= x < size and 0 <= y < size:
                        self.set_function(x, y, max(abs(dx), abs(dy)) not in (2, 4))
        positions = self.alignment_positions()
        last = len(positions) - 1
        for i, cx in enumerate(positions):
            for j, cy in enumerate(positions):
                if (i, j) in ((0, 0), (0, last), (last, 0)):
                    continue
                for dy in range(-2, 3):
                    for dx in range(-2, 3):
                        self.set_function(cx + dx, cy + dy, max(abs(dx), abs(dy)) != 1)
        self.draw_format(0)
        if self.version >= 7:
            remainder = self.version
            for _ in range(12):
                remainder = (remainder << 1) ^ ((remainder >> 11) * 0x1F25)
            bits = self.version << 12 | remainder
            for i in range(18):
                dark = (bits >> i) & 1 == 1
                a, b = size - 11 + i % 3, i // 3
                self.set_function(a, b, dark)
                self.set_function(b, a, dark)

    def draw_format(self, mask):
        data = _FORMAT_LEVEL_M << 3 | mask
        remainder = data
        for _ in range(10):
            remainder = (remainder << 1) ^ ((remainder >> 9) * 0x537)
        bits = (data << 10 | remainder) ^ 0x5412

        def bit(i):
            return (bits >> i) & 1 == 1

        size = self.size
        for i in range(6):
            self.set_function(8, i, bit(i))
        self.set_function(8, 7, bit(6))
        self.set_function(8, 8, bit(7))
        self.set_function(7, 8, bit(8))
        for i in range(9, 15):
            self.set_function(14 - i, 8, bit(i))
        for i in range(8):
            self.set_function(size - 1 - i, 8, bit(i))
        for i in range(8, 15):
            self.set_function(8, size - 15 + i, bit(i))
        self.set_function(8, size - 8, True)

    def draw_codewords(self, data):
        size, i = self.size, 0
        right = size - 1
        while right >= 1:
            if right == 6:
                right = 5
            for vertical in range(size):
                for j in range(2):
                    x = right - j
                    upward = (right + 1) & 2 == 0
                    y = size - 1 - vertical if upward else vertical
                    if not self.function[y][x] and i < len(data) * 8:
                        self.dark[y][x] = (data[i >> 3] >> (7 - (i & 7))) & 1 == 1
                        i += 1
            right -= 2

    def apply_mask(self, mask):
        test = _MASKS[mask]
        for y in range(self.size):
            for x in range(self.size):
                if not self.function[y][x] and test(x, y):
                    self.dark[y][x] = not self.dark[y][x]

    def penalty(self):
        size, dark, score = self.size, self.dark, 0
        lines = [row for row in dark] + [[dark[y][x] for y in range(size)] for x in range(size)]
        finder_like = ([True, False, True, True, True, False, True, False, False, False, False],
                       [False, False, False, False, True, False, True, True, True, False, True])
        for line in lines:
            run, previous = 0, None
            for value in line:
                if value == previous:
                    run += 1
                else:
                    if run >= 5:
                        score += run - 2
                    run, previous = 1, value
            if run >= 5:
                score += run - 2
            for start in range(size - 10):
                if line[start:start + 11] in finder_like:
                    score += 40
        for y in range(size - 1):
            for x in range(size - 1):
                if dark[y][x] == dark[y][x + 1] == dark[y + 1][x] == dark[y + 1][x + 1]:
                    score += 3
        total, area = sum(map(sum, dark)), size * size
        score += max(0, (abs(total * 20 - area * 10) + area - 1) // area - 1) * 10
        return score


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


def encode(text, mask=None):
    """Return the QR matrix for text as rows of booleans (True is dark).

    mask (0 to 7) forces one mask pattern; by default the lowest penalty wins.
    """
    payload = text.encode("utf-8")
    for version in range(1, MAX_VERSION + 1):
        header = 4 + (8 if version <= 9 else 16)
        if header + 8 * len(payload) <= _data_capacity(version) * 8:
            break
    else:
        raise ValueError("Text is too long for a share QR code.")
    data = _codewords(payload, version)
    best, best_score = None, None
    for candidate in range(8) if mask is None else (mask,):
        grid = _Grid(version)
        grid.draw_function_patterns()
        grid.draw_codewords(data)
        grid.apply_mask(candidate)
        grid.draw_format(candidate)
        score = grid.penalty()
        if best_score is None or score < best_score:
            best, best_score = grid, score
    return best.dark


QUIET = 4


def svg(matrix, *, dark="#0b0d12", light="#ffffff", label="QR code"):
    """A crisp, scalable SVG with a four-module quiet zone, one path, no scripts."""
    size = len(matrix) + 2 * QUIET
    parts = []
    for y, row in enumerate(matrix):
        x = 0
        while x < len(row):
            if row[x]:
                start = x
                while x < len(row) and row[x]:
                    x += 1
                parts.append(f"M{start + QUIET} {y + QUIET}h{x - start}v1h-{x - start}z")
            else:
                x += 1
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {size} {size}" shape-rendering="crispEdges" role="img">'
            f"<title>{escape(label)}</title>"
            f'<rect width="{size}" height="{size}" fill="{light}"/><path fill="{dark}" d="{"".join(parts)}"/></svg>')


def png(matrix, *, scale=16):
    """A greyscale PNG with a quiet zone, built with zlib only."""
    size = (len(matrix) + 2 * QUIET) * scale
    blank = b"\xff" * size
    rows = []
    for y in range(-QUIET, len(matrix) + QUIET):
        if 0 <= y < len(matrix):
            line = b"\xff" * (QUIET * scale) + b"".join(
                (b"\x0b" if value else b"\xff") * scale for value in matrix[y]) + b"\xff" * (QUIET * scale)
        else:
            line = blank
        rows.extend([b"\x00" + line] * scale)

    def chunk(kind, body):
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF)

    header = struct.pack(">IIBBBBB", size, size, 8, 0, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(b"".join(rows), 9)) + chunk(b"IEND", b"")
