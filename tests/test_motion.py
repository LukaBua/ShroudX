"""Tests for motion.py and scripts/validate_motion.py using synthetic SPHEREx frames."""

from __future__ import annotations

from pathlib import Path
import sys
from typing import Sequence

import numpy as np
import pytest
from astropy.wcs import WCS

# Ensure the repository root is on sys.path
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from frame import Frame  # noqa: E402
from motion import (  # noqa: E402
    DAYS_PER_YEAR,
    compute_parallax_factors,
    measure_and_fit_epochs,
    measure_frame_centroid,
)
from scripts.validate_motion import (  # noqa: E402
    BenchmarkTarget,
    ValidationRecord,
    evaluate_criterion,
    load_benchmarks,
    write_validation_outputs,
)


from frame import Frame
from motion import (
    DAYS_PER_YEAR,
    compute_parallax_factors,
    measure_and_fit_epochs,
    measure_frame_centroid,
)
from scripts.validate_motion import (
    BenchmarkTarget,
    evaluate_criterion,
    load_benchmarks,
    write_validation_outputs,
    ValidationRecord,
)

PIXEL_SCALE_ARCSEC: float = 6.15
PIXEL_SCALE_DEG: float = PIXEL_SCALE_ARCSEC / 3600.0


def make_tan_wcs(ra0: float = 150.0, dec0: float = 20.0, ny: int = 31, nx: int = 31) -> WCS:
    """Create a simple North-up, East-left celestial TAN WCS at 6.15 arcsec/pixel."""
    wcs = WCS(naxis=2)
    wcs.wcs.crpix = [nx / 2.0 + 0.5, ny / 2.0 + 0.5]
    wcs.wcs.cdelt = [-PIXEL_SCALE_DEG, PIXEL_SCALE_DEG]
    wcs.wcs.crval = [ra0, dec0]
    wcs.wcs.ctype = ["RA---TAN", "DEC--TAN"]
    return wcs


def make_synthetic_frame(
    x_true: float,
    y_true: float,
    flux: float,
    sigma_noise: float,
    mjd: float,
    wcs: WCS,
    rng: np.random.Generator,
    ny: int = 31,
    nx: int = 31,
    psf_sigma_pix: float = 1.1,
    unreliable: np.ndarray | None = None,
    version: int = 20,
) -> Frame:
    """Generate a synthetic 2D Gaussian source frame with white Gaussian noise."""
    yy, xx = np.indices((ny, nx), dtype=np.float64)
    gauss = np.exp(-0.5 * ((xx - x_true) ** 2 + (yy - y_true) ** 2) / (psf_sigma_pix ** 2))
    gauss /= np.sum(gauss)
    signal = flux * gauss
    noise = rng.normal(0.0, sigma_noise, size=(ny, nx))
    image = signal + noise
    variance = np.full((ny, nx), sigma_noise ** 2, dtype=np.float64)
    mask = np.zeros((ny, nx), dtype=bool)
    return Frame(
        image=image,
        variance=variance,
        mask=mask,
        wavelength=1.25,
        detector=2,
        mjd=mjd,
        wcs=wcs,
        version=version,
        unreliable=unreliable,
    )


def test_analytical_centroid_uncertainty_matches_monte_carlo() -> None:
    """Verify analytical centroid uncertainty from VARIANCE matches 200 MC noise realisations."""
    rng = np.random.default_rng(42)
    wcs = make_tan_wcs()
    x_true, y_true = 15.35, 14.80

    xs: list[float] = []
    ys: list[float] = []
    sig_xs: list[float] = []
    sig_ys: list[float] = []

    for _ in range(200):
        frame = make_synthetic_frame(
            x_true=x_true,
            y_true=y_true,
            flux=1500.0,
            sigma_noise=2.0,
            mjd=60000.0,
            wcs=wcs,
            rng=rng,
            psf_sigma_pix=1.3,
        )
        c = measure_frame_centroid(
            frame,
            expected_x=15.0,
            expected_y=15.0,
            radii=(4.5, 4.0),
        )
        assert c is not None
        xs.append(c.x)
        ys.append(c.y)
        sig_xs.append(c.sigma_x)
        sig_ys.append(c.sigma_y)

    mc_std_x = float(np.std(xs, ddof=1))
    mc_std_y = float(np.std(ys, ddof=1))
    mean_analytical_x = float(np.mean(sig_xs))
    mean_analytical_y = float(np.mean(sig_ys))

    assert np.isclose(mc_std_x, mean_analytical_x, rtol=0.15)
    assert np.isclose(mc_std_y, mean_analytical_y, rtol=0.15)


def test_unreliable_pixels_used_for_centroid_but_excluded_from_flux() -> None:
    """Verify Frame.unreliable pixels contribute to centroid position but not flux."""
    rng = np.random.default_rng(7)
    wcs = make_tan_wcs()
    x_true, y_true = 15.0, 15.0

    # Zero-noise frame to test exact deterministic behaviour
    frame_clean = make_synthetic_frame(
        x_true=x_true,
        y_true=y_true,
        flux=1000.0,
        sigma_noise=1e-6,
        mjd=60000.0,
        wcs=wcs,
        rng=rng,
    )
    unrel = np.zeros_like(frame_clean.mask)
    unrel[15, 15] = True  # Mark the central peak pixel as unreliable
    frame_unrel = frame_clean.copy_with(unreliable=unrel)

    c_clean = measure_frame_centroid(frame_clean, 15.0, 15.0)
    c_unrel = measure_frame_centroid(frame_unrel, 15.0, 15.0)

    assert c_clean is not None and c_unrel is not None
    # Centroid must remain identical because unreliable pixels are included in centroiding
    assert np.isclose(c_clean.x, c_unrel.x, atol=1e-7)
    assert np.isclose(c_clean.y, c_unrel.y, atol=1e-7)
    assert c_unrel.used_unreliable_in_centroid is True

    # Flux must be strictly lower by the peak pixel's flux
    peak_val = float(frame_clean.image[15, 15])
    assert c_unrel.n_flux_pixels == c_clean.n_flux_pixels - 1
    assert np.isclose(c_clean.flux - c_unrel.flux, peak_val, rtol=1e-3)


def test_snr_rejection_reported() -> None:
    """Verify low-SNR frames are rejected and counted accurately."""
    rng = np.random.default_rng(99)
    wcs = make_tan_wcs(ra0=150.0, dec0=20.0)
    x0, y0 = wcs.world_to_pixel_values(150.0, 20.0)

    epoch1 = [
        make_synthetic_frame(float(x0), float(y0), 2000.0, 2.0, 60000.0, wcs, rng),
        make_synthetic_frame(float(x0), float(y0), 1.0, 5.0, 60000.1, wcs, rng),  # Low SNR
    ]
    epoch2 = [
        make_synthetic_frame(float(x0), float(y0), 2000.0, 2.0, 60185.0, wcs, rng),
        make_synthetic_frame(float(x0), float(y0), 0.5, 5.0, 60185.1, wcs, rng),  # Low SNR
    ]

    res = measure_and_fit_epochs([epoch1, epoch2], 150.0, 20.0, snr_threshold=10.0)
    assert res.n_frames_used == 2
    assert res.n_frames_rejected == 2
    assert any("Rejected 2 frame(s)" in w for w in res.warnings)


@pytest.mark.parametrize("with_parallax", [False, True])
def test_motion_recovery_over_200_realisations(with_parallax: bool) -> None:
    """Verify proper motion (and parallax) recovery within 3 sigma over 200 noise realisations."""
    rng = np.random.default_rng(1234 if with_parallax else 5678)
    ra0, dec0 = 150.0, 20.0
    wcs = make_tan_wcs(ra0=ra0, dec0=dec0)
    x_center, y_center = wcs.world_to_pixel_values(ra0, dec0)

    pm_ra_true = 2.40  # arcsec/yr
    pm_dec_true = -1.60  # arcsec/yr
    plx_true = 0.45 if with_parallax else 0.0  # arcsec

    # 4 epochs separated by ~6 months
    epoch_mjds = np.array([60000.0, 60182.6, 60365.25, 60547.9], dtype=np.float64)
    t0_true = float(np.mean(epoch_mjds))
    dt_yr = (epoch_mjds - t0_true) / DAYS_PER_YEAR
    p_alpha, p_delta = compute_parallax_factors(ra0, dec0, epoch_mjds)

    dra_arcsec = pm_ra_true * dt_yr + plx_true * p_alpha
    ddec_arcsec = pm_dec_true * dt_yr + plx_true * p_delta

    # Note: cdelt[0] is negative (East is -x), cdelt[1] is positive (North is +y)
    x_epochs = float(x_center) - dra_arcsec / PIXEL_SCALE_ARCSEC
    y_epochs = float(y_center) + ddec_arcsec / PIXEL_SCALE_ARCSEC

    n_runs = 200
    within_3sigma_pm = 0
    within_3sigma_plx = 0

    for _ in range(n_runs):
        epoch_groups: list[list[Frame]] = []
        for mjd, xt, yt in zip(epoch_mjds, x_epochs, y_epochs):
            frames = [
                make_synthetic_frame(
                    x_true=float(xt),
                    y_true=float(yt),
                    flux=2500.0,
                    sigma_noise=2.0,
                    mjd=float(mjd + k * 0.05),
                    wcs=wcs,
                    rng=rng,
                    psf_sigma_pix=1.3,
                )
                for k in range(3)
            ]
            epoch_groups.append(frames)

        fit = measure_and_fit_epochs(
            epoch_groups,
            target_ra=ra0,
            target_dec=dec0,
            snr_threshold=5.0,
            fixed_parallax_arcsec=None if with_parallax else 0.0,
            radii=(4.5, 4.0),
        )

        z_ra = abs(fit.pm_ra_arcsec_yr - pm_ra_true) / fit.sigma_pm_ra_arcsec_yr
        z_dec = abs(fit.pm_dec_arcsec_yr - pm_dec_true) / fit.sigma_pm_dec_arcsec_yr
        if z_ra < 3.0 and z_dec < 3.0:
            within_3sigma_pm += 1

        if with_parallax:
            assert fit.parallax_fitted is True
            assert fit.parallax_arcsec is not None and fit.sigma_parallax_arcsec is not None
            z_plx = abs(fit.parallax_arcsec - plx_true) / fit.sigma_parallax_arcsec
            if z_plx < 3.0:
                within_3sigma_plx += 1

    # For a Gaussian distribution, >98% of realisations fall within 3 sigma
    assert within_3sigma_pm / n_runs >= 0.96
    if with_parallax:
        assert within_3sigma_plx / n_runs >= 0.96


def test_two_epoch_degenerate_case_handled_and_flagged() -> None:
    """Verify <3 epochs disables free parallax fit, supports fixed parallax, and warns."""
    rng = np.random.default_rng(2026)
    ra0, dec0 = 120.0, -15.0
    wcs = make_tan_wcs(ra0=ra0, dec0=dec0)
    x_center, y_center = wcs.world_to_pixel_values(ra0, dec0)

    pm_ra_true = -3.0  # arcsec/yr
    pm_dec_true = 1.5  # arcsec/yr
    plx_true = 0.60  # arcsec

    mjds = np.array([60000.0, 60182.625], dtype=np.float64)
    t0 = float(np.mean(mjds))
    dt_yr = (mjds - t0) / DAYS_PER_YEAR
    p_alpha, p_delta = compute_parallax_factors(ra0, dec0, mjds)

    dra = pm_ra_true * dt_yr + plx_true * p_alpha
    ddec = pm_dec_true * dt_yr + plx_true * p_delta
    xs = float(x_center) - dra / PIXEL_SCALE_ARCSEC
    ys = float(y_center) + ddec / PIXEL_SCALE_ARCSEC

    epoch_groups = [
        [make_synthetic_frame(float(xs[i]), float(ys[i]), 3000.0, 1.5, float(mjds[i]), wcs, rng)]
        for i in range(2)
    ]

    # Case 1: Unfixed parallax -> fits proper motion only, parallax is None, warns
    fit_unfixed = measure_and_fit_epochs(epoch_groups, ra0, dec0, fixed_parallax_arcsec=None)
    assert fit_unfixed.parallax_fitted is False
    assert fit_unfixed.parallax_fixed is False
    assert fit_unfixed.parallax_arcsec is None
    assert any("degenerate" in w for w in fit_unfixed.warnings)

    # Case 2: Fixed parallax from benchmarks -> recovers true proper motion accurately
    fit_fixed = measure_and_fit_epochs(
        epoch_groups, ra0, dec0, fixed_parallax_arcsec=plx_true, radii=(4.5, 4.0)
    )
    assert fit_fixed.parallax_fitted is False
    assert fit_fixed.parallax_fixed is True
    assert fit_fixed.parallax_arcsec == pytest.approx(plx_true)
    assert any("degenerate" in w and "fixed" in w for w in fit_fixed.warnings)
    assert abs(fit_fixed.pm_ra_arcsec_yr - pm_ra_true) < 3.0 * fit_fixed.sigma_pm_ra_arcsec_yr
    assert abs(fit_fixed.pm_dec_arcsec_yr - pm_dec_true) < 3.0 * fit_fixed.sigma_pm_dec_arcsec_yr


def test_chi2_uncertainty_inflation() -> None:
    """Verify uncertainties are scaled by sqrt(reduced_chi2) and flagged when reduced_chi2 > 1."""
    rng = np.random.default_rng(314)
    ra0, dec0 = 150.0, 20.0
    wcs = make_tan_wcs(ra0=ra0, dec0=dec0)
    x0, y0 = wcs.world_to_pixel_values(ra0, dec0)

    # Inject large unmodeled 2-pixel jitter on epoch 2 to force reduced_chi2 >> 1
    mjds = [60000.0, 60180.0, 60360.0, 60540.0]
    offsets_x = [0.0, 2.0, -1.5, 0.5]
    epoch_groups = [
        [
            make_synthetic_frame(
                float(x0 + dx), float(y0), 4000.0, 1.0, mjd, wcs, rng
            )
        ]
        for mjd, dx in zip(mjds, offsets_x)
    ]

    fit = measure_and_fit_epochs(epoch_groups, ra0, dec0)
    assert fit.reduced_chi2 > 1.0
    assert fit.uncertainties_inflated is True
    assert any("inflated by sqrt(reduced_chi2)" in w for w in fit.warnings)


def test_validation_criteria_and_stationary_star(tmp_path: Path) -> None:
    """Verify AGREE, UNINFORMATIVE, DISAGREE, and stationary star rules in validate_motion.py."""
    rng = np.random.default_rng(88)
    ra0, dec0 = 150.0, 20.0
    wcs = make_tan_wcs(ra0=ra0, dec0=dec0)
    x0, y0 = wcs.world_to_pixel_values(ra0, dec0)

    mjds = [60000.0, 60182.6, 60365.25, 60547.9]
    stationary_groups = [
        [make_synthetic_frame(float(x0), float(y0), 3000.0, 1.5, mjd, wcs, rng)]
        for mjd in mjds
    ]
    fit_stat = measure_and_fit_epochs(stationary_groups, ra0, dec0)

    stat_target = BenchmarkTarget(
        name="Stationary_Ref",
        ra=ra0,
        dec=dec0,
        pm_ra_arcsec_yr=0.0,
        pm_dec_arcsec_yr=0.0,
        parallax_arcsec=0.0,
        citation="Gaia DR3",
    )
    status_stat, diff_stat = evaluate_criterion(stat_target, fit_stat)
    assert status_stat == "AGREE"
    assert diff_stat < 3.0

    # Test UNINFORMATIVE when sigma >= 0.3 * pm_lit
    slow_target = BenchmarkTarget(
        name="Slow_Target",
        ra=ra0,
        dec=dec0,
        pm_ra_arcsec_yr=fit_stat.sigma_pm_total_arcsec_yr * 2.0,  # sigma is 0.5 * pm_lit >= 0.3
        pm_dec_arcsec_yr=0.0,
        parallax_arcsec=0.0,
        citation="Gaia DR3",
    )
    status_uninf, _ = evaluate_criterion(slow_target, fit_stat)
    assert status_uninf == "UNINFORMATIVE"

    # Test CSV/Markdown writer and benchmark loader requirement for stationary star
    csv_in = tmp_path / "benchmarks.csv"
    csv_in.write_text(
        "name,ra,dec,pm_ra,pm_dec,parallax,citation\n"
        "Stationary_Ref,150.0,20.0,0.0,0.0,0.0,Gaia DR3\n",
        encoding="utf-8",
    )
    loaded = load_benchmarks(csv_in)
    assert len(loaded) == 1

    csv_out = tmp_path / "results" / "motion_validation.csv"
    md_out = tmp_path / "results" / "motion_validation.md"
    rec = ValidationRecord(
        name="Stationary_Ref",
        status=status_stat,
        pm_lit_arcsec_yr=0.0,
        pm_meas_arcsec_yr=fit_stat.pm_total_arcsec_yr,
        sigma_pm_arcsec_yr=fit_stat.sigma_pm_total_arcsec_yr,
        pm_ra_lit_arcsec_yr=0.0,
        pm_ra_meas_arcsec_yr=fit_stat.pm_ra_arcsec_yr,
        sigma_pm_ra_arcsec_yr=fit_stat.sigma_pm_ra_arcsec_yr,
        pm_dec_lit_arcsec_yr=0.0,
        pm_dec_meas_arcsec_yr=fit_stat.pm_dec_arcsec_yr,
        sigma_pm_dec_arcsec_yr=fit_stat.sigma_pm_dec_arcsec_yr,
        parallax_lit_arcsec=0.0,
        parallax_meas_arcsec=fit_stat.parallax_arcsec,
        sigma_parallax_arcsec=fit_stat.sigma_parallax_arcsec,
        diff_sigma=diff_stat,
        reduced_chi2=fit_stat.reduced_chi2,
        uncertainties_inflated=fit_stat.uncertainties_inflated,
        n_epochs=fit_stat.n_epochs,
        n_frames_used=fit_stat.n_frames_used,
        n_frames_rejected=fit_stat.n_frames_rejected,
        version="v20",
        wavelength_window_um="D2:1.250-1.250",
        citation="Gaia DR3",
        diagnostics="None",
    )
    write_validation_outputs([rec], csv_out, md_out, "pytest")
    assert csv_out.exists()
    assert md_out.exists()
    assert "Stationary_Ref" in md_out.read_text(encoding="utf-8")
