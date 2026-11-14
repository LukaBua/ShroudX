# AI-assisted: written with AI pair-programming assistant.
"""Unit tests for stationary-star chromatic flicker experiment (experiments/flicker.py)."""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pytest
from astropy.wcs import WCS

from experiments.flicker import (
    FlickerStar,
    collect_star_photometry,
    compute_window_scatter,
    load_flicker_stars,
    run_flicker_experiment,
)
from frame import Frame
from processing import PIXEL_SCALE_ARCSEC


def _make_tan_wcs(ra: float, dec: float, ny: int = 25, nx: int = 25) -> WCS:
    """Create a North-up, East-left TAN WCS centered on (ra, dec)."""
    scale_deg = PIXEL_SCALE_ARCSEC / 3600.0
    wcs = WCS(naxis=2)
    wcs.wcs.ctype = ["RA---TAN", "DEC--TAN"]
    wcs.wcs.crval = [float(ra), float(dec)]
    wcs.wcs.crpix = [(nx + 1) / 2.0, (ny + 1) / 2.0]
    wcs.wcs.cdelt = [-scale_deg, scale_deg]
    wcs.wcs.cunit = ["deg", "deg"]
    wcs.wcs.radesys = "ICRS"
    return wcs


def _make_synthetic_star_frame(
    star: FlickerStar,
    *,
    wavelength_um: float,
    mjd: float,
    detector: int = 5,
    w0_um: float = 4.10,
    slope_per_um: float = 0.50,
    base_peak_flux: float = 400.0,
    sigma_psf_px: float = 1.1,
    pixel_sigma: float = 0.2,
    zodi_level: float = 10.0,
    mask_offset: tuple[int, int] | None = None,
    unreliable_offset: tuple[int, int] | None = None,
    seed: int = 0,
) -> Frame:
    """Generate a synthetic SPHEREx frame with a stationary star having a known flux-wavelength slope."""
    ny, nx = 25, 25
    wcs = _make_tan_wcs(star.ra, star.dec, ny=ny, nx=nx)
    x0, y0 = wcs.world_to_pixel_values(star.ra, star.dec)
    yy, xx = np.mgrid[0:ny, 0:nx]

    scale_factor = 1.0 + slope_per_um * (wavelength_um - w0_um)
    psf = np.exp(-0.5 * (((xx - float(x0)) ** 2 + (yy - float(y0)) ** 2) / (sigma_psf_px**2)))
    rng = np.random.default_rng(seed)
    noise = rng.normal(0.0, pixel_sigma, size=(ny, nx)) if pixel_sigma > 0 else np.zeros((ny, nx))
    zodi = np.full((ny, nx), zodi_level, dtype=np.float64)
    image = zodi + base_peak_flux * scale_factor * psf + noise
    variance = np.full((ny, nx), max(pixel_sigma**2, 1e-6), dtype=np.float64)

    mask = np.zeros((ny, nx), dtype=bool)
    if mask_offset is not None:
        dy, dx = mask_offset
        mask[int(round(float(y0))) + dy, int(round(float(x0))) + dx] = True

    unreliable = np.zeros((ny, nx), dtype=bool)
    if unreliable_offset is not None:
        dy, dx = unreliable_offset
        unreliable[int(round(float(y0))) + dy, int(round(float(x0))) + dx] = True

    return Frame(
        image=image,
        variance=variance,
        mask=mask,
        wavelength=wavelength_um,
        detector=detector,
        mjd=mjd,
        wcs=wcs,
        zodi=zodi,
        version=27,
        uri=f"synthetic://flicker/{star.name}_{mjd:.1f}.fits",
        unreliable=unreliable,
    )


def test_known_flux_wavelength_slope_recovers_expected_scatter(tmp_path: Path) -> None:
    """Synthetic stars with linear flux-wavelength slope recover analytical excess scatter."""
    stars = [
        FlickerStar(name="StarA", ra=100.0, dec=20.0, source="gaia_dr3"),
        FlickerStar(name="StarB", ra=150.0, dec=-10.0, source="gaia_dr3"),
        FlickerStar(name="StarC", ra=210.0, dec=45.0, source="gaia_dr3"),
    ]
    # Known fractional flux slope s = 0.50 um^-1 centered at median wavelength w0 = 4.10 um
    w0 = 4.10
    slope = 0.50
    wavelengths = np.linspace(3.90, 4.30, 9)  # 3.90, 3.95, 4.00, 4.05, 4.10, 4.15, 4.20, 4.25, 4.30
    mjds = np.array([60800.0 + 25.0 * i for i in range(len(wavelengths))])

    frames_by_star: dict[str, list[Frame]] = {}
    for s_idx, star in enumerate(stars):
        star_frames: list[Frame] = []
        for f_idx, (wl, mjd) in enumerate(zip(wavelengths, mjds, strict=True)):
            star_frames.append(
                _make_synthetic_star_frame(
                    star,
                    wavelength_um=float(wl),
                    mjd=float(mjd),
                    w0_um=w0,
                    slope_per_um=slope,
                    base_peak_flux=600.0,
                    pixel_sigma=0.05,
                    seed=100 * s_idx + f_idx,
                )
            )
        frames_by_star[star.name] = star_frames

    results_dir = tmp_path / "results"
    exp = run_flicker_experiment(
        stars=stars,
        frame_loader=lambda st: frames_by_star[st.name],
        results_dir=results_dir,
        threshold=0.05,
    )

    # Check that analytical pairwise scatter |s| * rms(delta_lambda) is recovered within tolerance
    by_w = {round(pt.window, 2): pt for pt in exp.wavelength_window_analysis}
    for w_test in (0.06, 0.12, 0.20, 0.30, 0.40):
        dws = [
            abs(float(wavelengths[i]) - float(wavelengths[j]))
            for i in range(len(wavelengths))
            for j in range(i + 1, len(wavelengths))
            if abs(float(wavelengths[i]) - float(wavelengths[j])) < w_test + 1e-12
        ]
        expected_excess = slope * math.sqrt(float(np.mean(np.square(dws))))
        measured_pt = by_w[round(w_test, 2)]
        assert measured_pt.n_pairs == 3 * len(dws)
        assert measured_pt.excess_scatter == pytest.approx(expected_excess, rel=0.04, abs=0.003)

    # At w = 0.10 um, only pairs with |dw| = 0.05 and 0.10 are included:
    # rms(dw) <= 0.10 -> excess_scatter <= 0.50 * 0.10 = 0.05; at w = 0.16 um it exceeds 0.05
    assert exp.recommendation.single_value_supported is True
    assert exp.recommendation.max_window_below_threshold_um is not None
    assert 0.10 <= exp.recommendation.max_window_below_threshold_um <= 0.14
    assert exp.recommendation.recommended_max_width_um == exp.recommendation.max_window_below_threshold_um

    # Verify JSON and PNG outputs were written
    json_path = results_dir / "flicker.json"
    assert json_path.is_file()
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert payload["frame_counts"]["usable"] == 27
    assert payload["frame_counts"]["skipped"] == 0
    assert payload["data_versions"] == ["l2b-v27"]
    assert len(list(results_dir.glob("flicker_*.png"))) == 2


def test_skipped_frame_counting_on_masked_and_unreliable_aperture() -> None:
    """Frames where the circular aperture touches masked or unreliable pixels are skipped and counted."""
    star = FlickerStar(name="MaskStar", ra=248.1676, dec=66.7550, source="gaia_dr3")
    frames = [
        # 5 clean frames
        _make_synthetic_star_frame(star, wavelength_um=4.05, mjd=60900.0, seed=1),
        _make_synthetic_star_frame(star, wavelength_um=4.07, mjd=60901.0, seed=2),
        _make_synthetic_star_frame(star, wavelength_um=4.10, mjd=60902.0, seed=3),
        _make_synthetic_star_frame(star, wavelength_um=4.12, mjd=60903.0, seed=4),
        _make_synthetic_star_frame(star, wavelength_um=4.15, mjd=60904.0, seed=5),
        # 1 frame with masked pixel at center (0, 0) inside r=2 aperture
        _make_synthetic_star_frame(
            star, wavelength_um=4.08, mjd=60905.0, mask_offset=(0, 0), seed=6
        ),
        # 1 frame with unreliable pixel at (1, 1) (distance sqrt(2) <= 2.0) inside aperture
        _make_synthetic_star_frame(
            star, wavelength_um=4.11, mjd=60906.0, unreliable_offset=(1, 1), seed=7
        ),
        # 1 frame with masked pixel at (0, 2) (distance 2.0 <= 2.0) touching aperture boundary
        _make_synthetic_star_frame(
            star, wavelength_um=4.13, mjd=60907.0, mask_offset=(0, 2), seed=8
        ),
        # 1 frame with masked pixel at (3, 3) (distance 4.24 > 2.0) outside aperture -> NOT skipped
        _make_synthetic_star_frame(
            star, wavelength_um=4.14, mjd=60908.0, mask_offset=(3, 3), seed=9
        ),
    ]

    summary = collect_star_photometry(star, frames, aperture_radius=2.0)
    assert summary.n_total_frames == 9
    assert summary.n_usable_frames == 6
    assert summary.n_skipped_frames == 3
    assert summary.detectors[5].n_total == 9
    assert summary.detectors[5].n_usable == 6
    assert summary.detectors[5].n_skipped == 3


def test_no_result_written_when_fewer_than_three_stars_have_five_usable_frames(
    tmp_path: Path,
) -> None:
    """run_flicker_experiment raises ValueError and writes no files if <3 stars have >=5 usable frames."""
    stars = [
        FlickerStar(name="Good1", ra=10.0, dec=10.0, source="gaia_dr3"),
        FlickerStar(name="Good2", ra=20.0, dec=20.0, source="gaia_dr3"),
        FlickerStar(name="TooFew", ra=30.0, dec=30.0, source="gaia_dr3"),
    ]

    def _loader(st: FlickerStar) -> list[Frame]:
        if st.name in ("Good1", "Good2"):
            return [
                _make_synthetic_star_frame(st, wavelength_um=4.05 + 0.02 * i, mjd=60900.0 + i, seed=i)
                for i in range(6)
            ]
        # TooFew has 6 total frames, but 2 have masked aperture pixels -> only 4 usable (< 5)
        return [
            _make_synthetic_star_frame(
                st,
                wavelength_um=4.05 + 0.02 * i,
                mjd=60900.0 + i,
                mask_offset=(0, 0) if i >= 4 else None,
                seed=50 + i,
            )
            for i in range(6)
        ]

    results_dir = tmp_path / "empty_results"
    with pytest.raises(ValueError, match="Insufficient usable stars"):
        run_flicker_experiment(
            stars=stars,
            frame_loader=_loader,
            results_dir=results_dir,
        )

    assert not (results_dir / "flicker.json").exists()
    assert list(results_dir.glob("flicker_*.png")) == []


def test_load_flicker_stars_reads_repository_csv() -> None:
    """load_flicker_stars parses data/flicker_stars.csv into FlickerStar records."""
    stars = load_flicker_stars()
    assert len(stars) >= 3
    for st in stars:
        assert st.name
        assert np.isfinite(st.ra)
        assert np.isfinite(st.dec)
        assert st.source
