# AI-assisted: written with AI pair-programming assistant.
"""Validate blind moving-object search on known movers and stationary control fields.

Pre-committed Validation Criteria (fixed before running on data):
1. Known-mover blind recovery (Barnard's Star and WISE 0855 from ``data/benchmarks.csv``):
   - ``movers.search_movers`` is invoked blindly with only field coordinates
     ``(ra, dec, fov=480.0)`` and no target name or prior proper motion.
   - ``RECOVERED``: At least one ``mover_candidate`` is found at rank <= 5 whose mean
     position lies within 20.0 arcsec of the literature position propagated to the
     observation epoch AND whose measured proper motion agrees with the literature
     proper motion within ``max(3.0 * pm_sigma, 0.35 * pm_literature)``.
   - ``MISSED``: No mover candidate satisfies the recovery criteria (or rank > 5).
   - ``INSUFFICIENT_DATA``: Fewer than 2 observation epochs exist in the selected window.
2. Stationary control fields (at least three fields with no known high-PM object,
   $\\mu_{\\mathrm{lit}} < 0.05''/\\mathrm{yr}$, from ``data/benchmarks.csv`` and
   ``data/flicker_stars.csv``: RW Aurigae, R Draconis, BD+60 1753):
   - Report both observed ``n_false_candidates`` (count of ``mover_candidate`` entries)
     and the 100-trial random-shift estimate ``expected_false_movers``.
   - ``CLEAN``: ``n_false_candidates <= max(2, ceil(expected_false_movers + 3 * sqrt(max(1.0, expected_false_movers))))``.
   - ``EXCESS_FALSE_POSITIVES``: ``n_false_candidates`` exceeds that bound.
3. Systematics investigation on ``MISSED`` or ``EXCESS_FALSE_POSITIVES``:
   - Automatically inspects and reports epoch baseline, excluded-source reasons
     (``near_edge``, ``masked_pixel``, ``unreliable_pixel``), and wavelength spread
     without tuning detection parameters.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import shlex
import sys
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import coords  # noqa: E402
import data  # noqa: E402
import movers  # noqa: E402
from frame import Frame  # noqa: E402

MATCH_RADIUS_ARCSEC: float = 20.0
MAX_RECOVERY_RANK: int = 5
DEFAULT_MAX_CUTOUTS_PER_FIELD: int = 36


@dataclass(frozen=True)
class MoverValidationField:
    """Specification of a known-mover or stationary control validation field."""

    name: str
    field_type: str  # "known_mover" or "control"
    ra_deg: float
    dec_deg: float
    pm_ra_lit_arcsec_yr: float | None
    pm_dec_lit_arcsec_yr: float | None
    pm_tot_lit_arcsec_yr: float | None
    pa_lit_deg: float | None
    citation: str


@dataclass(frozen=True)
class MoverValidationOutcome:
    """Validation result for one known-mover or control field."""

    name: str
    field_type: str
    verdict: str
    recovered: bool | None
    recovered_rank: int | None
    matched_candidate_id: str | None
    position_sep_arcsec: float | None
    pm_lit_arcsec_yr: float | None
    pm_meas_arcsec_yr: float | None
    pm_sigma_arcsec_yr: float | None
    pa_lit_deg: float | None
    pa_meas_deg: float | None
    pa_sigma_deg: float | None
    motion_significance: float | None
    n_mover_candidates: int
    expected_false_movers: float
    expected_false_movers_sigma: float
    analytic_false_movers: float
    n_sources_epoch_a: int
    n_sources_epoch_b: int
    n_excluded_sources: int
    baseline_years: float
    detector: int
    wavelength_window_um: tuple[float, float]
    data_versions: list[str]
    diagnostics: str
    citation: str
    disclaimer: str = movers.CANDIDATE_DISCLAIMER


def load_validation_fields(
    benchmarks_csv: str | Path = _REPO_ROOT / "data" / "benchmarks.csv",
    flicker_csv: str | Path = _REPO_ROOT / "data" / "flicker_stars.csv",
) -> list[MoverValidationField]:
    """Build the validation field list from ``data/benchmarks.csv`` and ``data/flicker_stars.csv``."""
    bpath = Path(benchmarks_csv)
    if not bpath.is_file():
        raise FileNotFoundError(f"Benchmarks CSV not found: {bpath}")

    known_movers: list[MoverValidationField] = []
    controls: list[MoverValidationField] = []

    with bpath.open("r", newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            name = (row.get("name") or "").strip()
            if not name:
                continue
            ra_cat = float(row["ra"])
            dec_cat = float(row["dec"])
            pmra_mas = float(row["pmra"])
            pmdec_mas = float(row["pmdec"])
            citation = (row.get("citation") or "").strip()

            pmra_arcsec = pmra_mas / 1000.0
            pmdec_arcsec = pmdec_mas / 1000.0
            pm_tot = math.hypot(pmra_arcsec, pmdec_arcsec)
            pa_deg = math.degrees(math.atan2(pmra_arcsec, pmdec_arcsec)) % 360.0

            # Propagate J2000 catalog position to 2026.0 survey epoch using coords.position_at_epoch
            ra_2026, dec_2026 = coords.position_at_epoch(
                ra_cat,
                dec_cat,
                pmra_mas,
                pmdec_mas,
                ref_epoch=2000.0,
                target_epoch=coords.DEFAULT_SURVEY_EPOCH,
            )

            lower = name.lower()
            if "barnard" in lower or "0855" in lower:
                known_movers.append(
                    MoverValidationField(
                        name=name,
                        field_type="known_mover",
                        ra_deg=float(ra_2026),
                        dec_deg=float(dec_2026),
                        pm_ra_lit_arcsec_yr=pmra_arcsec,
                        pm_dec_lit_arcsec_yr=pmdec_arcsec,
                        pm_tot_lit_arcsec_yr=pm_tot,
                        pa_lit_deg=pa_deg,
                        citation=citation,
                    )
                )
            elif pm_tot < 0.05:
                controls.append(
                    MoverValidationField(
                        name=name,
                        field_type="control",
                        ra_deg=float(ra_2026),
                        dec_deg=float(dec_2026),
                        pm_ra_lit_arcsec_yr=pmra_arcsec,
                        pm_dec_lit_arcsec_yr=pmdec_arcsec,
                        pm_tot_lit_arcsec_yr=pm_tot,
                        pa_lit_deg=pa_deg,
                        citation=citation,
                    )
                )

    fpath = Path(flicker_csv)
    if fpath.is_file():
        existing_names = {c.name.lower() for c in controls}
        with fpath.open("r", newline="", encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            for row in reader:
                if len(controls) >= 3:
                    break
                name = (row.get("name") or "").strip()
                if not name or name.lower() in existing_names:
                    continue
                controls.append(
                    MoverValidationField(
                        name=name,
                        field_type="control",
                        ra_deg=float(row["ra"]),
                        dec_deg=float(row["dec"]),
                        pm_ra_lit_arcsec_yr=0.0,
                        pm_dec_lit_arcsec_yr=0.0,
                        pm_tot_lit_arcsec_yr=0.0,
                        pa_lit_deg=None,
                        citation=(row.get("source") or "gaia_dr3").strip(),
                    )
                )

    return [*known_movers, *controls]


def _make_capped_tap_query(max_cutouts: int) -> Callable[[str], list[dict[str, Any]]]:
    """Cap deep-field TAP queries evenly across detectors and MJD."""

    def _tap_query(adql: str) -> list[dict[str, Any]]:
        try:
            from astroquery.ipac.irsa import Irsa

            res = Irsa.query_tap(adql)
            table = res.to_table() if hasattr(res, "to_table") else res
        except ImportError:
            import pyvo

            service = pyvo.dal.TAPService(data.IRSA_TAP_URL)
            res = service.search(adql)
            table = res.to_table() if hasattr(res, "to_table") else res

        rows: list[dict[str, Any]] = []
        colnames = set(getattr(table, "colnames", ()))
        for row in table:
            uri_val = row["uri"] if "uri" in colnames else row["a.uri"]
            mjd_val = row["mjd"] if "mjd" in colnames else row["time_bounds_lower"]
            band_val = (
                row["bandpass"]
                if "bandpass" in colnames
                else (row["energy_bandpassname"] if "energy_bandpassname" in colnames else row["band"])
            )
            if isinstance(uri_val, bytes):
                uri_val = uri_val.decode("utf-8")
            if isinstance(band_val, bytes):
                band_val = band_val.decode("utf-8")
            rows.append(
                {
                    "uri": str(uri_val).strip(),
                    "mjd": float(mjd_val),
                    "bandpass": str(band_val).strip(),
                }
            )

        if max_cutouts <= 0 or len(rows) <= max_cutouts:
            return rows

        by_band: dict[str, list[dict[str, Any]]] = {}
        for r in rows:
            by_band.setdefault(r["bandpass"], []).append(r)

        per_band = max(6, max_cutouts // max(1, len(by_band)))
        thinned: list[dict[str, Any]] = []
        for band_rows in by_band.values():
            band_rows.sort(key=lambda item: item["mjd"])
            if len(band_rows) <= per_band:
                thinned.extend(band_rows)
            else:
                idxs = np.linspace(0, len(band_rows) - 1, per_band, dtype=int)
                thinned.extend(band_rows[int(i)] for i in idxs)
        thinned.sort(key=lambda item: item["mjd"])
        return thinned[:max_cutouts]

    return _tap_query


def evaluate_field(
    vfield: MoverValidationField,
    *,
    fov_arcsec: float = movers.DEFAULT_FOV_ARCSEC,
    threshold: float = movers.DEFAULT_SNR_THRESHOLD,
    max_pm: float = movers.DEFAULT_MAX_PM_ARCSEC_YR,
    data_mode: str = "auto",
    cache_root: str | Path = _REPO_ROOT / "cache",
    max_cutouts_per_field: int = DEFAULT_MAX_CUTOUTS_PER_FIELD,
    frame_loader: Callable[[MoverValidationField], Sequence[Frame]] | None = None,
) -> MoverValidationOutcome:
    """Run blind ``movers.search_movers`` on ``vfield`` and evaluate against pre-committed criteria."""
    tap_fn = _make_capped_tap_query(max_cutouts_per_field) if max_cutouts_per_field > 0 else None

    try:
        if frame_loader is not None:
            raw_frames = frame_loader(vfield)
        else:
            raw_frames = data.get_frames(
                vfield.ra_deg,
                vfield.dec_deg,
                fov=fov_arcsec,
                mode=data_mode,
                cache_root=cache_root,
                tap_query=tap_fn,
            )
        search_res = movers.search_movers(
            vfield.ra_deg,
            vfield.dec_deg,
            fov=fov_arcsec,
            threshold=threshold,
            max_pm=max_pm,
            frames=raw_frames,
        )
    except Exception as exc:
        return MoverValidationOutcome(
            name=vfield.name,
            field_type=vfield.field_type,
            verdict="INSUFFICIENT_DATA",
            recovered=False if vfield.field_type == "known_mover" else None,
            recovered_rank=None,
            matched_candidate_id=None,
            position_sep_arcsec=None,
            pm_lit_arcsec_yr=vfield.pm_tot_lit_arcsec_yr,
            pm_meas_arcsec_yr=None,
            pm_sigma_arcsec_yr=None,
            pa_lit_deg=vfield.pa_lit_deg,
            pa_meas_deg=None,
            pa_sigma_deg=None,
            motion_significance=None,
            n_mover_candidates=0,
            expected_false_movers=0.0,
            expected_false_movers_sigma=0.0,
            analytic_false_movers=0.0,
            n_sources_epoch_a=0,
            n_sources_epoch_b=0,
            n_excluded_sources=0,
            baseline_years=0.0,
            detector=0,
            wavelength_window_um=(0.0, 0.0),
            data_versions=[],
            diagnostics=f"Blind search could not run: {exc}",
            citation=vfield.citation,
        )

    excl_reasons: dict[str, int] = {}
    for ex in search_res.excluded_sources:
        excl_reasons[ex.reason] = excl_reasons.get(ex.reason, 0) + 1
    excl_summary = (
        ", ".join(f"{k}={v}" for k, v in sorted(excl_reasons.items()))
        if excl_reasons
        else "none"
    )
    wl_lo, wl_hi = search_res.wavelength_window_um
    wl_spread = wl_hi - wl_lo

    if vfield.field_type == "known_mover":
        best_cand: movers.MoverCandidate | None = None
        best_sep: float | None = None
        for cand in search_res.candidates:
            if cand.ra_epoch_a is None or cand.dec_epoch_a is None or cand.ra_epoch_b is None or cand.dec_epoch_b is None:
                continue
            mean_ra = 0.5 * (cand.ra_epoch_a + cand.ra_epoch_b)
            mean_dec = 0.5 * (cand.dec_epoch_a + cand.dec_epoch_b)
            _, _, sep = movers._sky_offset_arcsec(vfield.ra_deg, vfield.dec_deg, mean_ra, mean_dec)
            if sep <= MATCH_RADIUS_ARCSEC:
                best_cand = cand
                best_sep = sep
                break

        if best_cand is not None and best_cand.pm_arcsec_per_yr is not None and best_cand.pm_sigma_arcsec_per_yr is not None:
            pm_lit = float(vfield.pm_tot_lit_arcsec_yr or 0.0)
            pm_diff = abs(best_cand.pm_arcsec_per_yr - pm_lit)
            tol = max(3.0 * best_cand.pm_sigma_arcsec_per_yr, 0.35 * pm_lit)
            rank_ok = best_cand.rank is not None and best_cand.rank <= MAX_RECOVERY_RANK
            pm_ok = pm_diff <= tol
            recovered = bool(rank_ok and pm_ok)
            verdict = "RECOVERED" if recovered else "MISSED"
            diag = (
                f"Recovered at rank {best_cand.rank} (sep={best_sep:.2f}\", "
                f"|dPM|={pm_diff:.2f}\"/yr <= tol={tol:.2f}\"/yr; "
                f"baseline={search_res.baseline_years:.3f} yr, d_lambda={wl_spread:.3f} um, "
                f"excluded={excl_summary})."
                if recovered
                else f"Candidate near target had rank={best_cand.rank}, |dPM|={pm_diff:.2f}\"/yr "
                f"(tol={tol:.2f}\"/yr); baseline={search_res.baseline_years:.3f} yr, "
                f"d_lambda={wl_spread:.3f} um, excluded={excl_summary}."
            )
            return MoverValidationOutcome(
                name=vfield.name,
                field_type=vfield.field_type,
                verdict=verdict,
                recovered=recovered,
                recovered_rank=best_cand.rank,
                matched_candidate_id=best_cand.id,
                position_sep_arcsec=best_sep,
                pm_lit_arcsec_yr=vfield.pm_tot_lit_arcsec_yr,
                pm_meas_arcsec_yr=best_cand.pm_arcsec_per_yr,
                pm_sigma_arcsec_yr=best_cand.pm_sigma_arcsec_per_yr,
                pa_lit_deg=vfield.pa_lit_deg,
                pa_meas_deg=best_cand.pa_deg,
                pa_sigma_deg=best_cand.pa_sigma_deg,
                motion_significance=best_cand.motion_significance,
                n_mover_candidates=search_res.n_mover_candidates,
                expected_false_movers=search_res.expected_false_movers,
                expected_false_movers_sigma=search_res.expected_false_movers_sigma,
                analytic_false_movers=search_res.analytic_false_movers,
                n_sources_epoch_a=search_res.n_sources_epoch_a,
                n_sources_epoch_b=search_res.n_sources_epoch_b,
                n_excluded_sources=len(search_res.excluded_sources),
                baseline_years=search_res.baseline_years,
                detector=search_res.detector,
                wavelength_window_um=search_res.wavelength_window_um,
                data_versions=search_res.data_versions,
                diagnostics=diag,
                citation=vfield.citation,
            )

        diag_miss = (
            f"Target not recovered within {MATCH_RADIUS_ARCSEC:.1f}\". "
            f"Systematics check: detector={search_res.detector}, "
            f"baseline={search_res.baseline_years:.3f} yr, "
            f"search_radius={search_res.search_radius_arcsec:.2f}\", "
            f"d_lambda={wl_spread:.3f} um, "
            f"sources=(A:{search_res.n_sources_epoch_a}, B:{search_res.n_sources_epoch_b}), "
            f"excluded=({excl_summary})."
        )
        return MoverValidationOutcome(
            name=vfield.name,
            field_type=vfield.field_type,
            verdict="MISSED",
            recovered=False,
            recovered_rank=None,
            matched_candidate_id=None,
            position_sep_arcsec=None,
            pm_lit_arcsec_yr=vfield.pm_tot_lit_arcsec_yr,
            pm_meas_arcsec_yr=None,
            pm_sigma_arcsec_yr=None,
            pa_lit_deg=vfield.pa_lit_deg,
            pa_meas_deg=None,
            pa_sigma_deg=None,
            motion_significance=None,
            n_mover_candidates=search_res.n_mover_candidates,
            expected_false_movers=search_res.expected_false_movers,
            expected_false_movers_sigma=search_res.expected_false_movers_sigma,
            analytic_false_movers=search_res.analytic_false_movers,
            n_sources_epoch_a=search_res.n_sources_epoch_a,
            n_sources_epoch_b=search_res.n_sources_epoch_b,
            n_excluded_sources=len(search_res.excluded_sources),
            baseline_years=search_res.baseline_years,
            detector=search_res.detector,
            wavelength_window_um=search_res.wavelength_window_um,
            data_versions=search_res.data_versions,
            diagnostics=diag_miss,
            citation=vfield.citation,
        )

    # Control field evaluation
    exp_false = search_res.expected_false_movers
    upper_bound = max(2, int(math.ceil(exp_false + 3.0 * math.sqrt(max(1.0, exp_false)))))
    is_clean = search_res.n_mover_candidates <= upper_bound
    verdict = "CLEAN" if is_clean else "EXCESS_FALSE_POSITIVES"
    diag_ctrl = (
        f"Control field false candidates = {search_res.n_mover_candidates} "
        f"(random-shift expected = {exp_false:.2f} +/- {search_res.expected_false_movers_sigma:.2f}, "
        f"bound <= {upper_bound}; stationary={search_res.n_stationary}, "
        f"unmatched={search_res.n_unmatched}, d_lambda={wl_spread:.3f} um, "
        f"excluded={excl_summary})."
    )
    return MoverValidationOutcome(
        name=vfield.name,
        field_type=vfield.field_type,
        verdict=verdict,
        recovered=None,
        recovered_rank=None,
        matched_candidate_id=None,
        position_sep_arcsec=None,
        pm_lit_arcsec_yr=vfield.pm_tot_lit_arcsec_yr,
        pm_meas_arcsec_yr=None,
        pm_sigma_arcsec_yr=None,
        pa_lit_deg=vfield.pa_lit_deg,
        pa_meas_deg=None,
        pa_sigma_deg=None,
        motion_significance=None,
        n_mover_candidates=search_res.n_mover_candidates,
        expected_false_movers=search_res.expected_false_movers,
        expected_false_movers_sigma=search_res.expected_false_movers_sigma,
        analytic_false_movers=search_res.analytic_false_movers,
        n_sources_epoch_a=search_res.n_sources_epoch_a,
        n_sources_epoch_b=search_res.n_sources_epoch_b,
        n_excluded_sources=len(search_res.excluded_sources),
        baseline_years=search_res.baseline_years,
        detector=search_res.detector,
        wavelength_window_um=search_res.wavelength_window_um,
        data_versions=search_res.data_versions,
        diagnostics=diag_ctrl,
        citation=vfield.citation,
    )


def write_validation_report(
    outcomes: Sequence[MoverValidationOutcome],
    *,
    json_path: Path,
    csv_path: Path,
    md_path: Path,
    command: str,
) -> None:
    """Write blind mover validation results to JSON, CSV, and Markdown."""
    json_path.parent.mkdir(parents=True, exist_ok=True)
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.parent.mkdir(parents=True, exist_ok=True)

    versions = sorted({v for o in outcomes for v in o.data_versions if v})
    payload = {
        "disclaimer": movers.CANDIDATE_DISCLAIMER,
        "command": command,
        "data_versions": versions if versions else ["unknown"],
        "criteria": {
            "known_mover_match_radius_arcsec": MATCH_RADIUS_ARCSEC,
            "known_mover_max_rank": MAX_RECOVERY_RANK,
            "known_mover_pm_tolerance": "max(3.0 * sigma_pm, 0.35 * pm_lit)",
            "control_field_max_false_candidates": "max(2, ceil(expected_false + 3 * sqrt(max(1, expected_false))))",
        },
        "fields": [movers._clean_json(asdict(o)) for o in outcomes],
    }
    json_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    fieldnames = list(MoverValidationOutcome.__dataclass_fields__.keys())
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        fh.write(f"# {movers.CANDIDATE_DISCLAIMER}\n")
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for o in outcomes:
            row = asdict(o)
            row["wavelength_window_um"] = f"{o.wavelength_window_um[0]:.3f}-{o.wavelength_window_um[1]:.3f}"
            row["data_versions"] = ";".join(o.data_versions)
            writer.writerow(row)

    lines = [
        "# SPHEREx Blind Moving-Object Search Validation",
        "",
        f"> **{movers.CANDIDATE_DISCLAIMER}**",
        "",
        f"- **Command:** `{command}`",
        f"- **Data versions:** `{', '.join(versions if versions else ['unknown'])}`",
        "",
        "| Field | Type | Verdict | Recovered (Rank) | PM Lit (\"/yr) | PM Meas +/- Sigma (\"/yr) | Candidates | Expected False +/- SE | Diagnostics |",
        "| :--- | :--- | :--- | :--- | ---: | :--- | ---: | :--- | :--- |",
    ]
    for o in outcomes:
        rec_str = (
            f"{o.recovered} (rank {o.recovered_rank})"
            if o.field_type == "known_mover"
            else "N/A (control)"
        )
        pm_lit_str = f"{o.pm_lit_arcsec_yr:.3f}" if o.pm_lit_arcsec_yr is not None else "N/A"
        pm_meas_str = (
            f"{o.pm_meas_arcsec_yr:.3f} +/- {o.pm_sigma_arcsec_yr:.3f}"
            if o.pm_meas_arcsec_yr is not None and o.pm_sigma_arcsec_yr is not None
            else "N/A"
        )
        lines.append(
            f"| {o.name} | {o.field_type} | **{o.verdict}** | {rec_str} | {pm_lit_str} | "
            f"{pm_meas_str} | {o.n_mover_candidates} | "
            f"{o.expected_false_movers:.2f} +/- {o.expected_false_movers_sigma:.2f} | {o.diagnostics} |"
        )
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point for ``scripts/validate_movers.py``."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--benchmarks", type=Path, default=_REPO_ROOT / "data" / "benchmarks.csv")
    parser.add_argument("--flicker-stars", type=Path, default=_REPO_ROOT / "data" / "flicker_stars.csv")
    parser.add_argument("--results-dir", type=Path, default=_REPO_ROOT / "results")
    parser.add_argument("--cache-root", type=Path, default=_REPO_ROOT / "cache")
    parser.add_argument("--mode", type=str, default="auto", choices=("cache", "live", "auto", "synthetic"))
    parser.add_argument("--fov", type=float, default=movers.DEFAULT_FOV_ARCSEC)
    parser.add_argument("--threshold", type=float, default=movers.DEFAULT_SNR_THRESHOLD)
    parser.add_argument("--max-pm", type=float, default=movers.DEFAULT_MAX_PM_ARCSEC_YR)
    parser.add_argument("--max-cutouts-per-field", type=int, default=DEFAULT_MAX_CUTOUTS_PER_FIELD)
    args = parser.parse_args(argv)

    cmd = "python " + " ".join(
        shlex.quote(a) for a in (sys.argv if argv is None else ["scripts/validate_movers.py", *argv])
    )
    fields = load_validation_fields(args.benchmarks, args.flicker_stars)
    outcomes: list[MoverValidationOutcome] = []
    for idx, vf in enumerate(fields, start=1):
        print(
            f"[{idx}/{len(fields)}] Running blind mover search on {vf.name} "
            f"({vf.field_type}, RA={vf.ra_deg:.4f}, Dec={vf.dec_deg:.4f})...",
            flush=True,
        )
        outcome = evaluate_field(
            vf,
            fov_arcsec=args.fov,
            threshold=args.threshold,
            max_pm=args.max_pm,
            data_mode=args.mode,
            cache_root=args.cache_root,
            max_cutouts_per_field=args.max_cutouts_per_field,
        )
        print(f"  -> {outcome.verdict}: {outcome.diagnostics}", flush=True)
        outcomes.append(outcome)

    out_dir = Path(args.results_dir)
    write_validation_report(
        outcomes,
        json_path=out_dir / "movers_validation.json",
        csv_path=out_dir / "movers_validation.csv",
        md_path=out_dir / "movers_validation.md",
        command=cmd,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
