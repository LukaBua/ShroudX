"""frame.py - the shared data contract between backend and statistician."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np


@dataclass
class Frame:
    """One SPHEREx cutout.

    image     (ny, nx) float array, MJy/sr. Zodiacal light is NOT subtracted.
    variance  (ny, nx) float array, (MJy/sr)^2.
    mask      (ny, nx) bool array, True = bad pixel (built from FLAGS).
    wavelength  wavelength at the target position, micrometres.
    detector  integer 1 to 6.
    mjd       observation time, Modified Julian Date (days).
    unreliable  optional (ny, nx) bool array, True = saturated/non-linear core pixel (see below).
    wcs       astropy WCS of the cutout, or None for mock data.
    """

    image: np.ndarray
    variance: np.ndarray
    mask: np.ndarray
    wavelength: float
    detector: int
    mjd: float
    wcs: Any | None = None
    meta: dict | None = None
    zodi: np.ndarray | None = None  # modelled zodiacal light, NOT yet subtracted from image
    version: int | None = None  # processing version, e.g. 20 for l2b-v20
    uri: str | None = None  # where the frame came from
    # True = flux unreliable (OVERFLOW / SUR_ERROR / NONLINEAR / PERSIST flags). Not part of `mask`:
    # these pixels stay usable for centroiding but are excluded from difference statistics.
    unreliable: np.ndarray | None = None
