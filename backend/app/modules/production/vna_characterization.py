"""Conservative eligibility check for an attached two-port Touchstone characterization.

This only makes a file available to a future de-embedding step. It neither applies
coefficients nor establishes their physical orientation or measurement accuracy.
"""

import math


def characterization_band(content: bytes) -> tuple[float, float] | None:
    if not content or len(content) > 2 * 1024 * 1024:
        return None
    try:
        text = content.decode("utf-8-sig")
        options, numbers = None, []
        for raw in text.splitlines():
            line = raw.split("!", 1)[0].strip()
            if not line:
                continue
            if line.startswith("#"):
                if options is not None or numbers:
                    return None
                options = line[1:].upper().split()
            else:
                numbers.extend(float(value) for value in line.split())
        if options is None or len(options) != 5:
            return None
        unit, parameter, encoding, reference, ohms = options
        scale = {"HZ": 1e-9, "KHZ": 1e-6, "MHZ": .001, "GHZ": 1}.get(unit)
        if scale is None or parameter != "S" or encoding not in ("RI", "MA", "DB") or reference != "R" or float(ohms) != 50:
            return None
        if len(numbers) < 18 or len(numbers) % 9 or not all(math.isfinite(value) for value in numbers):
            return None
        frequencies = numbers[::9]
        if frequencies[0] < 0 or any(a >= b for a, b in zip(frequencies, frequencies[1:])):
            return None
        return frequencies[0] * scale, frequencies[-1] * scale
    except (UnicodeError, ValueError, OverflowError):
        return None
