"""SPHEREx observation frame representation and validation."""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from typing import Any

import numpy as np
from astropy.wcs import WCS


@dataclass(eq=False)
class Frame:
    """Represents a single SPHEREx cutout observation frame.

    Attributes:
        image: 2D float array (ny, nx) in MJy/sr, zodiacal light not subtracted.
        variance: 2D float array (ny, nx) in (MJy/sr)^2.
        mask: 2D boolean array (ny, nx), True indicates a bad detector pixel.
        wavelength: Observation wavelength at the target pixel in micrometres.
        detector: SPHEREx detector index (1 to 6).
        mjd: Observation epoch as Modified Julian Date.
        wcs: Optional Astropy celestial WCS.
        zodi: Optional 2D float array (ny, nx) in MJy/sr of zodiacal light model.
        version: Optional processing pipeline version (e.g. 20 for l2b-v20).
        uri: Optional source file URI or path.
        unreliable: Optional 2D boolean array (ny, nx) marking saturated or
            non-linear pixels that remain usable for display and centroiding
            but must be excluded from high-precision photometry.
        meta: Optional metadata dictionary preserving FITS header attributes.
    """

    image: np.ndarray
    variance: np.ndarray
    mask: np.ndarray
    wavelength: float
    detector: int
    mjd: float
    wcs: WCS | None = None
    zodi: np.ndarray | None = None
    version: int | None = None
    uri: str | None = None
    unreliable: np.ndarray | None = None
    meta: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        """Validate fields upon initialization."""
        self.validate()

    def validate(self) -> None:
        """Validate frame dimensions, detector bounds, and coordinate values.

        Raises:
            ValueError: If array shapes mismatch, image is not 2D, mask or
                unreliable arrays are not bool dtype, detector is not an integer
                in 1..6, or wavelength/mjd are non-finite.
        """
        if not isinstance(self.image, np.ndarray) or self.image.ndim != 2:
            ndim = getattr(self.image, "ndim", None)
            raise ValueError(f"image must be a 2D numpy array (ny, nx), got ndim={ndim}")

        ny, nx = self.image.shape

        if not isinstance(self.variance, np.ndarray) or self.variance.shape != (ny, nx):
            var_shape = getattr(self.variance, "shape", None)
            raise ValueError(f"variance shape {var_shape} does not match image shape {(ny, nx)}")

        if not isinstance(self.mask, np.ndarray) or self.mask.shape != (ny, nx):
            mask_shape = getattr(self.mask, "shape", None)
            raise ValueError(f"mask shape {mask_shape} does not match image shape {(ny, nx)}")
        if self.mask.dtype != bool:
            raise ValueError(f"mask dtype must be bool, got {self.mask.dtype}")

        if self.zodi is not None:
            if not isinstance(self.zodi, np.ndarray) or self.zodi.shape != (ny, nx):
                zodi_shape = getattr(self.zodi, "shape", None)
                raise ValueError(f"zodi shape {zodi_shape} does not match image shape {(ny, nx)}")

        if self.unreliable is not None:
            if not isinstance(self.unreliable, np.ndarray) or self.unreliable.shape != (ny, nx):
                unrel_shape = getattr(self.unreliable, "shape", None)
                raise ValueError(
                    f"unreliable shape {unrel_shape} does not match image shape {(ny, nx)}"
                )
            if self.unreliable.dtype != bool:
                raise ValueError(f"unreliable dtype must be bool, got {self.unreliable.dtype}")

        if (
            isinstance(self.detector, bool)
            or not isinstance(self.detector, (int, np.integer))
            or not (1 <= self.detector <= 6)
        ):
            raise ValueError(f"detector must be an integer between 1 and 6, got {self.detector}")

        if not np.isfinite(self.wavelength):
            raise ValueError(f"wavelength must be a finite number, got {self.wavelength}")

        if not np.isfinite(self.mjd):
            raise ValueError(f"mjd must be a finite number, got {self.mjd}")

    @property
    def sigma(self) -> np.ndarray:
        """Calculate pixel uncertainty sigma = sqrt(variance).

        Values corresponding to non-positive variance (<= 0 or non-finite)
        are returned as NaN.

        Returns:
            2D numpy array of uncertainties matching the image shape.
        """
        valid = np.isfinite(self.variance) & (self.variance > 0)
        out_dtype = np.float32 if self.variance.dtype == np.float32 else np.float64
        result = np.full(self.variance.shape, np.nan, dtype=out_dtype)
        np.sqrt(self.variance, out=result, where=valid)
        return result

    def copy_with(self, **changes: Any) -> "Frame":
        """Return a new Frame instance with specified fields replaced.

        Args:
            **changes: Field names and their replacement values.

        Returns:
            A new validated Frame instance.
        """
        return dataclasses.replace(self, **changes)
