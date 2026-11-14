"""flags.py - turn the SPHEREx FLAGS layer into a bad-pixel mask.

Bit numbers come from the MP_* keywords in the FLAGS extension header (IRSA
tutorial "Introduction to SPHEREx Spectral Images"). The meaning of each bit
is described in Table 8 of the SPHEREx Explanatory Supplement.

IMPORTANT: do NOT use `flags != 0` as the mask. Most pixels carry the SOURCE
bit (a pixel mapped to a known source) and that does not make them bad, so
`flags != 0` would throw away most of the image.

STATUS: Confirmed against Table 8 and validated on real flight cutouts
(WISE 0855 and Barnard's Star).
"""

from __future__ import annotations

from typing import Any

import numpy as np

FLAG_BITS = {
    "TRANSIENT": 0,  # transient detected during SUR
    "OVERFLOW": 1,  # overflow detected during SUR
    "SUR_ERROR": 2,  # error in onboard processing
    "PHANTOM": 4,  # phantom pixel
    "REFERENCE": 5,  # reference pixel
    "NONFUNC": 6,  # permanently unusable
    "DICHROIC": 7,  # low efficiency due to dichroic
    "MISSING_DATA": 9,  # onboard data lost
    "HOT": 10,  # hot pixel
    "COLD": 11,  # anomalously low signal
    "FULLSAMPLE": 12,  # full sample history available (information only)
    "PHANMISS": 14,  # phantom correction was not applied
    "NONLINEAR": 15,  # nonlinearity correction cannot be applied reliably
    "PERSIST": 17,  # persistent charge above threshold
    "OUTLIER": 19,  # flagged by Detect Outliers
    "SOURCE": 21,  # mapped to a known source (information only, very common)
}

# Categorized flag groups based on Table 8 severity:
# Dead/broken detector pixels with no physical photon signal (safe to mask & inpaint on cutouts):
DETECTOR_DEFECTS = (
    "REFERENCE",
    "NONFUNC",
    "MISSING_DATA",
    "HOT",
    "COLD",
)
HARD_DEFECTS = (
    "OVERFLOW",
    "SUR_ERROR",
    "REFERENCE",
    "NONFUNC",
    "MISSING_DATA",
    "HOT",
    "COLD",
)
CALIBRATION_DEFECTS = (
    "PHANTOM",
    "DICHROIC",
    "PHANMISS",
    "NONLINEAR",
    "PERSIST",
)
TRANSIENTS = (
    "TRANSIENT",
    "OUTLIER",
)
# Pixels whose FLUX is unreliable (saturated, non-linear, persistence) but whose POSITION is fine.
# They stay usable for centroiding and blink display, but are excluded from difference statistics,
# because a core that reads low in one epoch looks like a fake "negative lobe".
UNRELIABLE_CORE = (
    "OVERFLOW",
    "SUR_ERROR",
    "NONLINEAR",
    "PERSIST",
)
# Information flags: do not mask on these.
INFORMATION_ONLY = ("FULLSAMPLE", "SOURCE")

# Standard bad bits: hard defects, calibration flaws, and transients
BAD_BITS = tuple(bit for name, bit in FLAG_BITS.items() if name not in INFORMATION_ONLY)

# Permissive bad bits: preserve transients/outliers so moving objects aren't masked
PERMISSIVE_BAD_BITS = tuple(
    bit for name, bit in FLAG_BITS.items() if name not in INFORMATION_ONLY and name not in TRANSIENTS
)


def bad_bit_mask(names=None, include_transients: bool = True) -> int:
    """Integer with a 1 in every bit we treat as bad (or in the named bits).

    If names is None:
        - include_transients=True (default): masks all BAD_BITS.
        - include_transients=False: preserves TRANSIENT and OUTLIER so moving objects
          can be detected by multi-frame sigma-clipping instead of masked upfront.
    """
    if names is not None:
        bits = tuple(FLAG_BITS[n] for n in names)
    elif include_transients:
        bits = BAD_BITS
    else:
        bits = PERMISSIVE_BAD_BITS

    value = 0
    for b in bits:
        value |= 1 << b
    return value


def flags_to_mask(flags, names=None, include_transients: bool = True) -> np.ndarray:
    """Boolean array, True = bad pixel."""
    mask_int = bad_bit_mask(names=names, include_transients=include_transients)
    return (np.asarray(flags).astype(np.int64) & mask_int) != 0


def active_flags(flag_val: int) -> list[str]:
    """Return list of human-readable flag names active for a given integer flag value."""
    val = int(flag_val)
    return [name for name, bit in FLAG_BITS.items() if (val >> bit) & 1]


def explain_pixel(flag_val: int) -> dict[str, Any]:
    """Detailed breakdown of a pixel's quality status for interactive inspection."""
    active = active_flags(flag_val)
    active_set = set(active)

    has_hard = bool(active_set.intersection(HARD_DEFECTS))
    has_calib = bool(active_set.intersection(CALIBRATION_DEFECTS))
    has_transient = bool(active_set.intersection(TRANSIENTS))
    is_source = "SOURCE" in active_set

    is_bad = has_hard or has_calib or has_transient

    if has_hard:
        category = "hard_defect"
    elif has_calib:
        category = "calibration_defect"
    elif has_transient:
        category = "transient"
    elif is_source:
        category = "source"
    else:
        category = "clean"

    return {
        "flag_value": int(flag_val),
        "is_bad": is_bad,
        "category": category,
        "active_flags": active,
        "is_source": is_source,
    }


def flag_layers(flags: np.ndarray, active_only: bool = True) -> dict[str, np.ndarray]:
    """Return boolean masks for individual flag types (useful for UI overlay toggles).

    If active_only is True (default), returns only flags that appear at least once.
    """
    f = np.asarray(flags).astype(np.int64)
    layers = {}
    for name, bit in FLAG_BITS.items():
        layer = ((f >> bit) & 1) != 0
        if not active_only or layer.any():
            layers[name] = layer
    return layers


def describe(flags) -> dict:
    """How many pixels carry each flag (useful for the first look at a cutout)."""
    f = np.asarray(flags).astype(np.int64)
    return {name: int(((f >> bit) & 1).sum()) for name, bit in FLAG_BITS.items()}
