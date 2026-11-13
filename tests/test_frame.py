"""Unit tests for SPHEREx Frame dataclass."""

import numpy as np
import pytest
from astropy.wcs import WCS

from frame import Frame


def make_valid_frame(
    ny: int = 10,
    nx: int = 10,
    detector: int = 1,
    wavelength: float = 0.95,
    mjd: float = 60000.0,
    **kwargs,
) -> Frame:
    """Helper creating a minimal valid Frame for tests."""
    defaults = {
        "image": np.ones((ny, nx), dtype=np.float32),
        "variance": np.full((ny, nx), 4.0, dtype=np.float32),
        "mask": np.zeros((ny, nx), dtype=bool),
        "wavelength": wavelength,
        "detector": detector,
        "mjd": mjd,
    }
    defaults.update(kwargs)
    return Frame(**defaults)


def test_valid_frame_minimal() -> None:
    """Verify that a minimal valid frame instantiates and passes validation."""
    frame = make_valid_frame()
    assert frame.image.shape == (10, 10)
    assert frame.detector == 1
    assert frame.wavelength == 0.95
    assert frame.mjd == 60000.0
    assert frame.wcs is None
    assert frame.zodi is None
    assert frame.unreliable is None
    assert frame.version is None
    assert frame.uri is None
    assert frame.meta is None


def test_valid_frame_with_all_optional_fields() -> None:
    """Verify that a frame with all optional fields instantiates cleanly."""
    shape = (8, 8)
    wcs = WCS(naxis=2)
    zodi = np.full(shape, 0.5, dtype=np.float32)
    unreliable = np.zeros(shape, dtype=bool)
    unreliable[0, 0] = True

    frame = Frame(
        image=np.ones(shape, dtype=np.float32),
        variance=np.ones(shape, dtype=np.float32),
        mask=np.zeros(shape, dtype=bool),
        wavelength=2.1,
        detector=3,
        mjd=60123.4,
        wcs=wcs,
        zodi=zodi,
        version=20,
        uri="s3://spherex/test.fits",
        unreliable=unreliable,
        meta={"ORIGIN": "IRSA", "BAND": 3},
    )
    frame.validate()
    assert frame.version == 20
    assert frame.uri == "s3://spherex/test.fits"
    assert frame.meta["BAND"] == 3
    assert bool(frame.unreliable[0, 0]) is True


@pytest.mark.parametrize("det", [1, 2, 3, 4, 5, 6])
def test_valid_detectors(det: int) -> None:
    """Verify that all SPHEREx detectors (1 to 6) are valid."""
    frame = make_valid_frame(detector=det)
    assert frame.detector == det


@pytest.mark.parametrize("invalid_det", [0, -1, 7, 8, 100, 1.5, True, False, "1"])
def test_invalid_detector_raises(invalid_det) -> None:
    """Verify that invalid detector indices raise ValueError."""
    with pytest.raises(ValueError, match="detector must be an integer between 1 and 6"):
        make_valid_frame(detector=invalid_det)


def test_image_not_2d_raises() -> None:
    """Verify that 1D or 3D image arrays raise ValueError."""
    with pytest.raises(ValueError, match="image must be a 2D numpy array"):
        make_valid_frame(image=np.ones(10, dtype=np.float32))

    with pytest.raises(ValueError, match="image must be a 2D numpy array"):
        make_valid_frame(image=np.ones((2, 2, 2), dtype=np.float32))

    with pytest.raises(ValueError, match="image must be a 2D numpy array"):
        make_valid_frame(image=[[1.0, 2.0], [3.0, 4.0]])


def test_variance_shape_mismatch_raises() -> None:
    """Verify that variance shape mismatch raises ValueError."""
    with pytest.raises(ValueError, match="variance shape .* does not match image shape"):
        make_valid_frame(variance=np.ones((10, 8), dtype=np.float32))


def test_mask_shape_mismatch_raises() -> None:
    """Verify that mask shape mismatch raises ValueError."""
    with pytest.raises(ValueError, match="mask shape .* does not match image shape"):
        make_valid_frame(mask=np.zeros((8, 10), dtype=bool))


def test_zodi_shape_mismatch_raises() -> None:
    """Verify that zodi shape mismatch raises ValueError."""
    with pytest.raises(ValueError, match="zodi shape .* does not match image shape"):
        make_valid_frame(zodi=np.zeros((8, 8), dtype=np.float32))


def test_unreliable_shape_mismatch_raises() -> None:
    """Verify that unreliable shape mismatch raises ValueError."""
    with pytest.raises(ValueError, match="unreliable shape .* does not match image shape"):
        make_valid_frame(unreliable=np.zeros((5, 5), dtype=bool))


@pytest.mark.parametrize("bad_val", [np.nan, np.inf, -np.inf])
def test_non_finite_wavelength_raises(bad_val: float) -> None:
    """Verify that non-finite wavelength raises ValueError."""
    with pytest.raises(ValueError, match="wavelength must be a finite number"):
        make_valid_frame(wavelength=bad_val)


@pytest.mark.parametrize("bad_val", [np.nan, np.inf, -np.inf])
def test_non_finite_mjd_raises(bad_val: float) -> None:
    """Verify that non-finite MJD raises ValueError."""
    with pytest.raises(ValueError, match="mjd must be a finite number"):
        make_valid_frame(mjd=bad_val)


def test_sigma_property() -> None:
    """Verify that sigma = sqrt(variance) and non-positive variance returns NaN."""
    variance = np.array(
        [
            [4.0, 9.0],
            [0.0, -2.5],
            [np.nan, 16.0],
        ],
        dtype=np.float32,
    )
    image = np.zeros_like(variance)
    mask = np.zeros_like(variance, dtype=bool)

    frame = Frame(
        image=image,
        variance=variance,
        mask=mask,
        wavelength=1.5,
        detector=2,
        mjd=60000.0,
    )

    sigma = frame.sigma
    assert sigma.shape == variance.shape
    assert sigma[0, 0] == pytest.approx(2.0)
    assert sigma[0, 1] == pytest.approx(3.0)
    assert np.isnan(sigma[1, 0])  # zero variance
    assert np.isnan(sigma[1, 1])  # negative variance
    assert np.isnan(sigma[2, 0])  # nan variance
    assert sigma[2, 1] == pytest.approx(4.0)


def test_copy_with() -> None:
    """Verify that copy_with returns a new instance with updated fields."""
    orig = make_valid_frame(mjd=60000.0, detector=1)
    updated = orig.copy_with(mjd=60050.0, detector=2)

    assert orig.mjd == 60000.0
    assert orig.detector == 1
    assert updated.mjd == 60050.0
    assert updated.detector == 2
    assert updated.wavelength == orig.wavelength
    assert np.array_equal(updated.image, orig.image)


def test_copy_with_invalid_raises() -> None:
    """Verify that copy_with validates the newly produced instance."""
    orig = make_valid_frame()
    with pytest.raises(ValueError, match="detector must be an integer between 1 and 6"):
        orig.copy_with(detector=9)


def test_frame_identity_equality_and_hashable() -> None:
    """Verify that two frames with identical contents are not equal and can be put in a set."""
    frame1 = make_valid_frame()
    frame2 = make_valid_frame()

    # Even with identical contents, frames compare by identity
    assert frame1 != frame2
    assert frame1 == frame1

    # Frames must be hashable and usable in sets / dict keys
    frame_set = {frame1, frame2}
    assert len(frame_set) == 2
    assert frame1 in frame_set
    assert frame2 in frame_set


def test_mask_dtype_validation() -> None:
    """Verify that int8 mask raises ValueError and bool mask passes."""
    bool_frame = make_valid_frame(mask=np.zeros((10, 10), dtype=bool))
    assert bool_frame.mask.dtype == bool

    with pytest.raises(ValueError, match="mask dtype must be bool"):
        make_valid_frame(mask=np.zeros((10, 10), dtype=np.int8))


def test_unreliable_dtype_validation() -> None:
    """Verify that unreliable array with non-bool dtype raises ValueError."""
    with pytest.raises(ValueError, match="unreliable dtype must be bool"):
        make_valid_frame(unreliable=np.zeros((10, 10), dtype=np.int8))
