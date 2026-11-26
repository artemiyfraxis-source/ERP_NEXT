"""Decode clean test rasters, independently of label encoding/placement.

Only QR versions 1/2 at level M (one RS block), byte/alphanumeric/numeric modes,
and Code128 B are needed by these fixtures. This does not simulate scanner optics
or repair damaged symbols; warehouse hardware verification remains separate.
"""

from itertools import groupby


def decode_code128(image):
    from app.modules.warehouse.labels.code128 import PATTERNS

    y = image.height // 2
    runs = [(value, len(list(pixels))) for value, pixels in groupby(image.getpixel((x, y)) for x in range(image.width))]
    while runs and runs[0][0]:
        runs.pop(0)
    while runs and runs[-1][0]:
        runs.pop()
    unit = min(length for _, length in runs)
    widths = "".join(str(length // unit) for _, length in runs)
    assert widths[-7:] == "2331112"
    symbols = [PATTERNS.index(widths[index:index + 6]) for index in range(0, len(widths) - 7, 6)]
    assert symbols[0] == 104
    assert symbols[-1] == (104 + sum(index * value for index, value in enumerate(symbols[1:-1], 1))) % 103
    return "".join(chr(value + 32) for value in symbols[1:-1])


def decode_qr(image, n, unit):
    # Locate symbol ink inside the quiet zone; finder patterns touch all four symbol edges.
    left, top, right, bottom = image.point(lambda value: 255 - value).getbbox()
    assert right - left == bottom - top == n * unit
    matrix = [[not image.getpixel((left + x * unit + unit // 2, top + y * unit + unit // 2))
               for x in range(n)] for y in range(n)]
    assert n in (21, 25)
    # Read the redundant vertical format string, least significant bit first.
    encoded = sum(int(matrix[i if i < 6 else i + 1 if i < 8 else n - 15 + i][8]) << i for i in range(15))
    masks_m = (0x5412, 0x5125, 0x5E7C, 0x5B4B, 0x45F9, 0x40CE, 0x4F97, 0x4AA0)
    mask = masks_m.index(encoded)
    masks = (
        lambda x, y: (x + y) % 2 == 0, lambda x, y: y % 2 == 0,
        lambda x, y: x % 3 == 0, lambda x, y: (x + y) % 3 == 0,
        lambda x, y: (y // 2 + x // 3) % 2 == 0, lambda x, y: (x * y) % 2 + (x * y) % 3 == 0,
        lambda x, y: ((x * y) % 2 + (x * y) % 3) % 2 == 0,
        lambda x, y: ((x + y) % 2 + (x * y) % 3) % 2 == 0,
    )

    def reserved(x, y):
        return (x <= 8 and y <= 8 or x >= n - 8 and y <= 8 or x <= 8 and y >= n - 8
                or x == 6 or y == 6 or n == 25 and 16 <= x <= 20 and 16 <= y <= 20)

    bits, right_column, upward = [], n - 1, True
    while right_column > 0:
        if right_column == 6:
            right_column -= 1
        for y in range(n - 1, -1, -1) if upward else range(n):
            for x in (right_column, right_column - 1):
                if not reserved(x, y):
                    bits.append(int(matrix[y][x] != masks[mask](x, y)))
        upward = not upward
        right_column -= 2

    cursor = 0

    def take(count):
        nonlocal cursor
        result = int("".join(map(str, bits[cursor:cursor + count])), 2)
        cursor += count
        return result

    mode = take(4)
    if mode == 4:
        return bytes(take(8) for _ in range(take(8))).decode("utf-8")
    if mode == 2:
        alphabet = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ $%*+-./:"
        count = take(9)
        text = ""
        for _ in range(count // 2):
            value = take(11)
            text += alphabet[value // 45] + alphabet[value % 45]
        return text + (alphabet[take(6)] if count % 2 else "")
    assert mode == 1
    count = take(10)
    text = "".join(f"{take(10):03d}" for _ in range(count // 3))
    return text + (f"{take(7):02d}" if count % 3 == 2 else str(take(4)) if count % 3 else "")
