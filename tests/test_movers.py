# AI-assisted: written with AI pair-programming assistant.
"""Unit tests for blind moving-object search (movers.py and scripts/validate_movers.py)."""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pytest
from astropy.wcs import WCS

import movers
from frame import Frame
from processing import PIXEL_SCALE_ARCSEC
from scripts.validate_movers import (
    MoverValidationField,
    evaluate_field,
    load_validation_fields,
)


def _make_tan_wcs(ra: float = 180.0, dec: float = 0.0, ny: int = 64, nx: int = 64) -> WCS:
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


def _add_gaussian_source(
    img: np.ndarray,
    x0: float,
    y0: float,
    peak_flux: float = 80.0,
    sigma_px: float = 1.0,
) -> None:
    """Add a 2D Gaussian source in-place to ``img``."""
    ny, nx = img.shape
    yy, xx = np.mgrid[0:ny, 0:nx]
    img += peak_flux * np.exp(-0.5 * (((xx - x0) ** 2 + (yy - y0) ** 2) / (sigma_px**2)))


def test_injected_mover_recovered_stationary_not_flagged_and_edge_excluded(
    tmp_path: Path,
) -> None:
    """One injected mover is recovered at rank 1; stationary stars are not flagged; edge/bad sources excluded."""
    rng = np.random.default_rng(1234)
    ny, nx = 64, 64
    ra0, dec0 = 150.0, 10.0
    wcs = _make_tan_wcs(ra=ra0, dec=dec0, ny=ny, nx=nx)

    # Stationary stars well inside the field
    stationary_coords = [
        (12.0, 14.0, 120.0),
        (48.0, 15.0, 100.0),
        (15.0, 48.0, 110.0),
        (46.0, 46.0, 95.0),
    ]
    # Injected mover shifts by (+1.0 px in x, +0.8 px in y) over 1.0 yr baseline
    # Offset = hypot(1.0, 0.8) * 6.15 = 1.2806 * 6.15 = 7.876 arcsec (<= 15"/yr * 1 yr)
    mover_ep0 = (30.0, 30.0)
    mover_ep1 = (31.0, 30.8)

    # Edge source at (x=1.0, y=32.0) within 3 px of cutout edge -> must be excluded
    edge_coord = (1.0, 32.0)
    # Masked-aperture source at (x=25.0, y=15.0) -> must be excluded
    masked_coord = (25.0, 15.0)
    # Unreliable-aperture source at (x=38.0, y=15.0) -> must be excluded
    unrel_coord = (38.0, 15.0)

    frames: list[Frame] = []
    # Two epochs separated by exactly 365.25 days (1.0 year)
    for ep_idx, base_mjd in enumerate((60800.0, 61165.25)):
        mx, my = mover_ep0 if ep_idx == 0 else mover_ep1
        for k in range(3):
            img = rng.normal(0.0, 1.0, size=(ny, nx))
            for sx, sy, sflux in stationary_coords:
                _add_gaussian_source(img, sx, sy, peak_flux=sflux, sigma_px=1.0)
            _add_gaussian_source(img, mx, my, peak_flux=130.0, sigma_px=1.0)
            _add_gaussian_source(img, edge_coord[0], edge_coord[1], peak_flux=150.0, sigma_px=1.0)
            _add_gaussian_source(img, masked_coord[0], masked_coord[1], peak_flux=140.0, sigma_px=1.0)
            _add_gaussian_source(img, unrel_coord[0], unrel_coord[1], peak_flux=140.0, sigma_px=1.0)

            mask = np.zeros((ny, nx), dtype=bool)
            mask[int(masked_coord[1]), int(masked_coord[0])] = True

            unreliable = np.zeros((ny, nx), dtype=bool)
            unreliable[int(unrel_coord[1]), int(unrel_coord[0])] = True

            frames.append(
                Frame(
                    image=img,
                    variance=np.ones((ny, nx), dtype=np.float64),
                    mask=mask,
                    wavelength=4.12,
                    detector=5,
                    mjd=base_mjd + 0.2 * k,
                    wcs=wcs.deepcopy(),
                    version=27,
                    unreliable=unreliable,
                )
            )

    res = movers.search_movers_in_frames(
        frames,
        ra=ra0,
        dec=dec0,
        fov_arcsec=ny * PIXEL_SCALE_ARCSEC,
        threshold=5.0,
        max_pm=15.0,
        clean_and_align=False,
    )

    # Exactly 1 mover candidate recovered at rank 1; all 4 stationary stars classified as stationary
    assert res.n_mover_candidates == 1
    assert res.n_stationary == 4
    assert len(res.candidates) == 1

    cand = res.candidates[0]
    assert cand.rank == 1
    assert cand.classification == "mover_candidate"
    expected_offset = math.hypot(1.0, 0.8) * PIXEL_SCALE_ARCSEC
    assert cand.offset_arcsec == pytest.approx(expected_offset, abs=0.45)
    assert cand.pm_arcsec_per_yr == pytest.approx(expected_offset / 1.0, abs=0.45)
    assert cand.motion_significance is not None and cand.motion_significance > 10.0
    assert "candidate_not_detection" in cand.quality_flags

    # Edge, masked, and unreliable sources were excluded and their reasons recorded
    reasons = {ex.reason for ex in res.excluded_sources}
    assert "near_edge" in reasons
    assert "masked_pixel" in reasons
    assert "unreliable_pixel" in reasons

    # CSV and JSON outputs state clearly that entries are candidates, not detections
    csv_out, json_out = movers.write_movers_outputs(
        res,
        tmp_path / "movers.csv",
        tmp_path / "movers.json",
        command="pytest test_movers.py",
    )
    csv_text = csv_out.read_text(encoding="utf-8")
    json_payload = json.loads(json_out.read_text(encoding="utf-8"))
    assert "candidates, not confirmed detections" in csv_text.lower()
    assert "candidates, not confirmed detections" in json_payload["disclaimer"].lower()
    assert len(json_payload["candidates"]) == 1


def test_random_shift_false_match_estimate_matches_analytic_expectation() -> None:
    """100-trial random-shift false-match rate agrees with the analytic expectation for known density."""
    rng = np.random.default_rng(2026)
    field_w = 600.0  # arcsec
    field_h = 600.0  # arcsec
    area = field_w * field_h
    ra_c, dec_c = 180.0, 0.0
    n_a = 45
    n_b = 45
    search_radius = 12.0  # arcsec
    baseline_yr = 1.0

    # Generate uniformly distributed synthetic DetectedSource catalogs in Epoch A and Epoch B
    def _make_catalog(n_src: int, epoch: int) -> list[movers.DetectedSource]:
        xs_arc = rng.uniform(-0.48 * field_w, 0.48 * field_w, size=n_src)
        ys_arc = rng.uniform(-0.48 * field_h, 0.48 * field_h, size=n_src)
        out: list[movers.DetectedSource] = []
        for idx in range(n_src):
            ra_s = ra_c + float(xs_arc[idx]) / 3600.0
            dec_s = dec_c + float(ys_arc[idx]) / 3600.0
            out.append(
                movers.DetectedSource(
                    epoch=epoch,
                    source_index=idx,
                    x_peak=20,
                    y_peak=20,
                    x=20.0,
                    y=20.0,
                    sigma_x=0.05,
                    sigma_y=0.05,
                    ra=ra_s,
                    dec=dec_s,
                    sigma_ra_arcsec=0.25,
                    sigma_dec_arcsec=0.25,
                    flux=100.0,
                    flux_sigma=5.0,
                    snr=20.0,
                    peak_snr=15.0,
                )
            )
        return out

    sources_a = _make_catalog(n_a, epoch=0)
    sources_b = _make_catalog(n_b, epoch=1)

    mean_false, se_false, analytic_exp = movers.estimate_false_mover_rate(
        sources_a,
        sources_b,
        baseline_years=baseline_yr,
        search_radius_arcsec=search_radius,
        field_width_arcsec=field_w,
        field_height_arcsec=field_h,
        ra_center=ra_c,
        dec_center=dec_c,
        n_trials=100,
        seed=99,
    )

    # Analytic expectation ~ N_A * (N_B / A) * pi * (R^2 - r_3sigma^2) ~ 45 * (45 / 360000) * pi * 144 ~ 2.54
    assert analytic_exp > 1.5
    assert abs(mean_false - analytic_exp) <= max(3.5 * se_false, 0.20 * analytic_exp)


def test_validate_movers_script_on_synthetic_known_mover_and_controls() -> None:
    """validate_movers loads known movers + >=3 controls and evaluates blind recovery."""
    fields = load_validation_fields()
    known = [f for f in fields if f.field_type == "known_mover"]
    controls = [f for f in fields if f.field_type == "control"]
    assert len(known) >= 2
    assert len(controls) >= 3

    # Build a synthetic field for a known mover where the mover shifts at its literature PM
    target = known[0]
    ny, nx = 64, 64
    wcs = _make_tan_wcs(ra=target.ra_deg, dec=target.dec_deg, ny=ny, nx=nx)
    cx, cy = (nx - 1) / 2.0, (ny - 1) / 2.0

    # Over 0.5 yr baseline, shift by 0.5 * (pm_ra, pm_dec) in sky coordinates
    dt_yr = 0.5
    dra_arcsec = float(target.pm_ra_lit_arcsec_yr or 0.0) * dt_yr
    ddec_arcsec = float(target.pm_dec_lit_arcsec_yr or 0.0) * dt_yr
    # East is -x, North is +y
    dx_px = -dra_arcsec / PIXEL_SCALE_ARCSEC
    dy_px = ddec_arcsec / PIXEL_SCALE_ARCSEC

    synth_frames: list[Frame] = []
    for ep_idx, mjd in enumerate((60900.0, 60900.0 + dt_yr * 365.25)):
        sx = cx - 0.5 * dx_px if ep_idx == 0 else cx + 0.5 * dx_px
        sy = cy - 0.5 * dy_px if ep_idx == 0 else cy + 0.5 * dy_px
        for k in range(2):
            img = np.zeros((ny, nx), dtype=np.float64)
            _add_gaussian_source(img, sx, sy, peak_flux=150.0, sigma_px=1.0)
            _add_gaussian_source(img, 12.0, 12.0, peak_flux=120.0, sigma_px=1.0)
            synth_frames.append(
                Frame(
                    image=img,
                    variance=np.ones((ny, nx), dtype=np.float64),
                    mask=np.zeros((ny, nx), dtype=bool),
                    wavelength=4.12,
                    detector=5,
                    mjd=mjd + 0.1 * k,
                    wcs=wcs.deepcopy(),
                    version=27,
                )
            )

    outcome = evaluate_field(
        target,
        fov_arcsec=ny * PIXEL_SCALE_ARCSEC,
        frame_loader=lambda _vf: synth_frames,
    )
    assert outcome.verdict == "RECOVERED"
    assert outcome.recovered is True
    assert outcome.recovered_rank == 1

    # Stationary control field with only the stationary star produces 0 false mover candidates
    ctrl_frames: list[Frame] = []
    ctrl = controls[0]
    wcs_ctrl = _make_tan_wcs(ra=ctrl.ra_deg, dec=ctrl.dec_deg, ny=ny, nx=nx)
    for mjd in (60900.0, 61082.6):
        img = np.zeros((ny, nx), dtype=np.float64)
        _add_gaussian_source(img, 20.0, 20.0, peak_flux=140.0, sigma_px=1.0)
        _add_gaussian_source(img, 42.0, 38.0, peak_flux=110.0, sigma_px=1.0)
        ctrl_frames.append(
            Frame(
                image=img,
                variance=np.ones((ny, nx), dtype=np.float64),
                mask=np.zeros((ny, nx), dtype=bool),
                wavelength=4.12,
                detector=5,
                mjd=mjd,
                wcs=wcs_ctrl.deepcopy(),
                version=27,
            )
        )

    ctrl_outcome = evaluate_field(
        ctrl,
        fov_arcsec=ny * PIXEL_SCALE_ARCSEC,
        frame_loader=lambda _vf: ctrl_frames,
    )
    assert ctrl_outcome.verdict == "CLEAN"
    assert ctrl_outcome.n_mover_candidates == 0
