# AI-assisted: written with Claude (Anthropic).
"""Decode the SPHEREx FLAGS bitmask.

``FLAG_BITS`` is copied from the ``MP_*`` keywords of a real FLAGS extension
header (a cutout downloaded from IRSA). It must not be edited from memory: use
``verify_against_header`` to check it against a real header.

``EXTRA_FLAG_BITS`` holds bits that IRSA documents for newer processing
versions (SPHEREx Data Explorer, Spectrophotometry Tool help page, "Notes on
SPHEREx Photometry Flags") but that were not in the header we verified. Flag
definitions change between data releases, so run ``verify_against_header`` on
the files you actually use, and keep an eye on the processing version.

There is deliberately no function that masks every non-zero flag. The SOURCE
bit (21) is set on many perfectly good pixels, so ``flags != 0`` would throw
away good data.

Flags are treated as a 32-bit word, so a negative int32 value (bit 31 set)
is decoded as bit 31 and not as a sign extension.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

import numpy as np

# Name -> bit number. Names are the MP_* keyword suffixes from the header.
FLAG_BITS: dict[str, int] = {
    "TRANSIENT": 0,  # Transient detected during SUR
    "OVERFLOW": 1,  # Overflow detected during SUR
    "SUR_ERROR": 2,  # Error in onboard processing
    "PHANTOM": 4,  # Phantom pixel
    "REFERENCE": 5,  # Reference pixel
    "NONFUNC": 6,  # Permanently unusable
    "DICHROIC": 7,  # Low efficiency due to dichroic
    "MISSING_DATA": 9,  # Onboard data lost
    "HOT": 10,  # Hot pixel
    "COLD": 11,  # Anomalously low signal
    "FULLSAMPLE": 12,  # Pixel full sample history is available
    "PHANMISS": 14,  # Phantom correction was not applied
    "NONLINEAR": 15,  # Nonlinearity correction cannot be applied reliably
    "PERSIST": 17,  # Persistent charge above threshold
    "OUTLIER": 19,  # Pixel flagged by Detect Outliers
    "SOURCE": 21,  # Pixel mapped to a known source
}

# Bits documented by IRSA for newer processing versions, not seen in the real
# header above. IRSA gives no short definition for most of them. Their header
# keyword names are assumed to be MP_<NAME>; verify_against_header checks that.
EXTRA_FLAG_BITS: dict[str, int] = {
    "CROSSTALK": 20,
    "GHOST": 22,
    "GHOST_FPA": 23,  # IRSA: not yet in public images
    "GHOST_EXT": 24,  # IRSA: not yet in public images
    "STREAK": 25,
    "BLOOM": 26,
    "SNOWBALL": 27,
    "HALO": 28,
    "SATELLITE_HALO": 29,
}

ALL_FLAG_BITS: dict[str, int] = {**FLAG_BITS, **EXTRA_FLAG_BITS}

# Every flag is in exactly one group (checked by the tests).

# Pixels whose data are invalid or unusable (hardware defects, lost or
# corrupted onboard data, very low dichroic efficiency). Masked by default.
# IRSA's short definitions: NONFUNC "dead pixel", DICHROIC "unusable pixel",
# HOT "noisy pixel", COLD "unresponsive pixel", MISSING_DATA "no data",
# SUR_ERROR "possible errors". IRSA also ignores SUR_ERROR, NONFUNC,
# MISSING_DATA, HOT and COLD pixels in its own forced photometry.
DETECTOR_DEFECTS: frozenset[str] = frozenset(
    {
        "SUR_ERROR",
        "PHANTOM",
        "REFERENCE",
        "NONFUNC",
        "DICHROIC",
        "MISSING_DATA",
        "HOT",
        "COLD",
    }
)

# Saturated, non-linear or persistent pixels, or pixels with an uncorrected
# phantom signal: the position is fine (usable for display and centroiding)
# but the flux is unreliable. Not masked by default; used for Frame.unreliable.
# IRSA's short definitions: OVERFLOW "nearing saturation", NONLINEAR and
# PHANMISS "can't be corrected", PERSIST "persistent charge". Note that IRSA
# ignores NONLINEAR and PERSIST pixels even in its own forced photometry, so
# treat centroids that rely on them with care.
UNRELIABLE_CORE: frozenset[str] = frozenset({"OVERFLOW", "PHANMISS", "NONLINEAR", "PERSIST"})

# Pixels flagged as transient or outlier by the pipeline. IRSA's short
# definitions: TRANSIENT "e.g., CR hit" (cosmic rays, satellite trails),
# OUTLIER "flagged outlier". Not masked by default, so that this is a
# deliberate team decision: a cosmic ray left in a blink frame looks like a
# flash, while masking removes it. Use flags_to_mask(flags, DETECTOR_DEFECTS |
# TRANSIENTS) to mask both.
TRANSIENTS: frozenset[str] = frozenset({"TRANSIENT", "OUTLIER"})

# Informational only. Never mask on these: SOURCE is set on many good pixels.
INFORMATION_ONLY: frozenset[str] = frozenset({"FULLSAMPLE", "SOURCE"})

# Image artifacts documented by IRSA for newer processing versions (extra bits
# only). Ghosts, streaks and similar artifacts can mimic or hide a moving
# source, so they are surfaced here instead of being silently undefined. Not
# masked by default. IRSA ignores BLOOM, SNOWBALL, HALO and SATELLITE_HALO
# pixels in its own forced photometry. Masking HALO or BLOOM would also remove
# pixels around bright stars (such as Barnard's Star), so decide deliberately.
IMAGE_ARTIFACTS: frozenset[str] = frozenset(EXTRA_FLAG_BITS)

GROUPS: dict[str, frozenset[str]] = {
    "DETECTOR_DEFECTS": DETECTOR_DEFECTS,
    "UNRELIABLE_CORE": UNRELIABLE_CORE,
    "TRANSIENTS": TRANSIENTS,
    "INFORMATION_ONLY": INFORMATION_ONLY,
    "IMAGE_ARTIFACTS": IMAGE_ARTIFACTS,
}

_BIT_TO_NAME: dict[int, str] = {bit: name for name, bit in ALL_FLAG_BITS.items()}
_WORD_MASK = 0xFFFFFFFF
_N_BITS = 32


def _as_words(flags: Any) -> tuple[np.ndarray, np.ndarray]:
    """Convert flag values to non-negative int64 words.

    Casting to int64 first avoids NumPy 2 raising OverflowError when a bit
    mask such as 1 << 21 is applied to a uint16 array.

    Returns:
        (words, nonfinite): words is an int64 array (non-finite entries set to
        0), nonfinite is a bool array of the same shape.

    Raises:
        TypeError: If the dtype is not integer or float.
        ValueError: If a finite float value is not a whole number.
    """
    arr = np.asarray(flags)
    kind = arr.dtype.kind
    if kind in "iu":
        words = arr.astype(np.int64)
        nonfinite = np.zeros(arr.shape, dtype=bool)
    elif kind == "f":
        nonfinite = ~np.isfinite(arr)
        safe = np.where(nonfinite, 0.0, arr)
        if np.any(safe != np.floor(safe)):
            raise ValueError("flag values must be whole numbers")
        words = safe.astype(np.int64)
    else:
        raise TypeError(f"flags must have an integer or float dtype, got {arr.dtype}")
    return words & _WORD_MASK, nonfinite


def _names_to_bitmask(names: Iterable[str] | str) -> int:
    """Combine flag names into one integer bit mask."""
    if isinstance(names, str):
        names = (names,)
    names = tuple(names)
    unknown = sorted(n for n in names if n not in ALL_FLAG_BITS)
    if unknown:
        raise ValueError(f"unknown flag names: {unknown}. Known: {sorted(ALL_FLAG_BITS)}")
    bitmask = 0
    for name in names:
        bitmask |= 1 << ALL_FLAG_BITS[name]
    return bitmask


def flags_to_mask(flags: Any, names: Iterable[str] | str = DETECTOR_DEFECTS) -> np.ndarray:
    """Return a bool array that is True where any of the named flags is set.

    Non-finite float flag values are treated as bad (True), because a missing
    flag value cannot be trusted.

    Args:
        flags: Array of flag values (any integer or float dtype).
        names: Flag names or a group such as ``DETECTOR_DEFECTS`` (default).

    Returns:
        Bool array with the same shape as ``flags``.

    Raises:
        ValueError: If a name is unknown or float flags are not whole numbers.
        TypeError: If the dtype is not integer or float.
    """
    bitmask = _names_to_bitmask(names)
    words, nonfinite = _as_words(flags)
    return ((words & bitmask) != 0) | nonfinite


def decode_flags(value: Any) -> list[str]:
    """Return the names of the flags set in one pixel value, ordered by bit.

    Set bits that have no definition are reported as ``UNDEFINED_BIT_<n>``
    instead of being dropped. A non-finite value gives ``["NON_FINITE"]``.

    Raises:
        ValueError: If ``value`` is not a single value.
    """
    words, nonfinite = _as_words(value)
    if words.ndim != 0:
        raise ValueError("decode_flags takes a single pixel value, not an array")
    if bool(nonfinite):
        return ["NON_FINITE"]
    word = int(words)
    return [
        _BIT_TO_NAME.get(bit, f"UNDEFINED_BIT_{bit}") for bit in range(_N_BITS) if (word >> bit) & 1
    ]


def summarize(flags: Any) -> dict[str, int]:
    """Count the pixels that have each flag set.

    Every flag in ``FLAG_BITS`` is always present (count 0 if unused), ordered
    by bit. Flags from ``EXTRA_FLAG_BITS``, undefined bits
    (``UNDEFINED_BIT_<n>``) and ``NON_FINITE`` appear only when they occur.
    """
    words, nonfinite = _as_words(flags)
    counts = {name: 0 for name in sorted(FLAG_BITS, key=FLAG_BITS.__getitem__)}
    for bit in range(_N_BITS):
        n = int(np.count_nonzero((words >> bit) & 1))
        if bit in _BIT_TO_NAME:
            name = _BIT_TO_NAME[bit]
            if n or name in FLAG_BITS:
                counts[name] = n
        elif n:
            counts[f"UNDEFINED_BIT_{bit}"] = n
    n_bad = int(np.count_nonzero(nonfinite))
    if n_bad:
        counts["NON_FINITE"] = n_bad
    return counts


def verify_against_header(header: Mapping[str, Any], strict: bool = True) -> list[str]:
    """Compare the flag tables with the MP_* keywords of a real FLAGS header.

    Args:
        header: The header of the FLAGS extension (an astropy Header or any
            mapping with ``keys()`` and item access).
        strict: If True, every flag in ``FLAG_BITS`` must also be in the
            header. Use ``strict=False`` for headers from newer processing
            versions, which may drop some flags. ``EXTRA_FLAG_BITS`` are never
            required.

    Returns:
        A list of problems; an empty list means the tables match the header.
    """
    in_header: dict[str, int] = {}
    for key in header.keys():  # noqa: SIM118 (astropy Header needs keys())
        if str(key).startswith("MP_"):
            in_header[str(key)[3:]] = int(header[key])
    problems: list[str] = []
    for name, bit in in_header.items():
        if name not in ALL_FLAG_BITS:
            problems.append(f"header has MP_{name} = {bit}, but we have no flag {name}")
        elif ALL_FLAG_BITS[name] != bit:
            problems.append(f"{name}: header says bit {bit}, we say {ALL_FLAG_BITS[name]}")
    if strict:
        for name in FLAG_BITS:
            if name not in in_header:
                problems.append(f"FLAG_BITS has {name}, but the header has no MP_{name}")
    return problems
