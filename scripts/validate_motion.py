"""Validate SPHEREx proper motion and parallax measurements against literature benchmarks.

Pre-committed Validation Criteria (fixed prior to running on data):
1. Stationary star benchmark (literature pm < 0.05 arcsec/yr = 50 mas/yr, sub-0.01 pixel/yr
   at SPHEREx 6.15"/pix scale):
   - AGREE: motion_significance = |pm_measured| / sigma_pm < 3.0 (consistent with zero).
   - DISAGREE: motion_significance >= 3.0.
2. Moving targets (literature pm >= 0.05 arcsec/yr):
   - UNINFORMATIVE: sigma_pm >= 0.3 * pm_literature (this is NOT a pass).
   - AGREE: sigma_pm < 0.3 * pm_literature AND
     vector difference |pm_vec_measured - pm_vec_literature| < 3.0 * sigma_pm.
   - DISAGREE: sigma_pm < 0.3 * pm_literature AND
     |pm_vec_measured - pm_vec_literature| >= 3.0 * sigma_pm.

Systematics Investigation on DISAGREE:
When a target returns DISAGREE, this script automatically inspects and reports
potential systematic drivers without tuning parameters:
- Epoch count and parallax degeneracy (fewer than 3 epochs without parallax correction).
- Presence of unreliable/saturated pixels inside the centroid aperture.
- Inter-frame scatter / WCS alignment residuals reflected by reduced chi-squared > 1.
- Wavelength spread within the selected group.
"""

from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import shlex
import sys
from typing import Sequence

import numpy as np

# Ensure repo root is importable whether invoked from repo root or scripts/
_SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = _SCRIPT_DIR.parent if _SCRIPT_DIR.name == "scripts" else _SCRIPT_DIR
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import coords  # noqa: E402
import data  # noqa: E402
from frame import Frame  # noqa: E402
from motion import MotionFitResult, measure_and_fit_epochs  # noqa: E402
import processing  # noqa: E402

# Threshold (arcsec/yr) below which a catalog star is effectively stationary at
# SPHEREx resolution (0.05"/yr = 50 mas/yr = 0.008 pix/yr at 6.15"/pix).
STATIONARY_PM_THRESHOLD_ARCSEC_YR: float = 0.05


@dataclass(frozen=True)
class BenchmarkTarget:
    """Single row from data/benchmarks.csv (converted to arcsec/yr and arcsec)."""

    name: str
    ra: float
    dec: float
    pm_ra_arcsec_yr: float
    pm_dec_arcsec_yr: float
    parallax_arcsec: float | None
    citation: str

    @property
    def pm_total_arcsec_yr(self) -> float:
        return float(np.hypot(self.pm_ra_arcsec_yr, self.pm_dec_arcsec_yr))

    @property
    def is_stationary(self) -> bool:
        return self.pm_total_arcsec_yr < STATIONARY_PM_THRESHOLD_ARCSEC_YR


@dataclass(frozen=True)
class ValidationRecord:
    """Validation outcome for a single benchmark target."""

    name: str
    status: str
    pm_lit_arcsec_yr: float
    pm_meas_arcsec_yr: float
    sigma_pm_arcsec_yr: float
    pm_ra_lit_arcsec_yr: float
    pm_ra_meas_arcsec_yr: float
    sigma_pm_ra_arcsec_yr: float
    pm_dec_lit_arcsec_yr: float
    pm_dec_meas_arcsec_yr: float
    sigma_pm_dec_arcsec_yr: float
    parallax_lit_arcsec: float | None
    parallax_meas_arcsec: float | None
    sigma_parallax_arcsec: float | None
    diff_sigma: float
    reduced_chi2: float
    uncertainties_inflated: bool
    n_epochs: int
    n_frames_used: int
    n_frames_rejected: int
    version: str
    wavelength_window_um: str
    citation: str
    diagnostics: str


def load_benchmarks(csv_path: Path) -> list[BenchmarkTarget]:
    """Read literature benchmark targets from data/benchmarks.csv.

    Supports both:
    - Gaia/SIMBAD standard columns `pmra`, `pmdec`, `parallax` in mas/yr and mas
      (divided by 1000.0 to convert to arcsec/yr and arcsec).
    - Explicit arcsec columns `pm_ra`, `pm_dec`, `parallax_arcsec` in arcsec/yr and arcsec.
    """
    if not csv_path.exists():
        raise FileNotFoundError(
            f"Benchmark file not found at {csv_path}. Populate data/benchmarks.csv "
            "from SIMBAD/Gaia/literature with citations before running validation."
        )

    targets: list[BenchmarkTarget] = []
    with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        for row_idx, raw_row in enumerate(reader, start=2):
            row = {
                k.strip().lower(): (v.strip() if v is not None else "")
                for k, v in raw_row.items()
                if k is not None
            }
            if not any(row.values()):
                continue

            name = row["name"]
            ra, dec = coords.parse_position(row["ra"], row["dec"])

            # `pmra` / `pmdec` (Gaia/SIMBAD standard in mas/yr) vs `pm_ra` / `pm_dec` (arcsec/yr)
            if "pmra" in row and row["pmra"] != "":
                pm_ra = float(row["pmra"]) / 1000.0
                pm_dec = float(row["pmdec"]) / 1000.0
                plx_scale = 1000.0
            elif "pm_ra_mas_yr" in row and row["pm_ra_mas_yr"] != "":
                pm_ra = float(row["pm_ra_mas_yr"]) / 1000.0
                pm_dec = float(row["pm_dec_mas_yr"]) / 1000.0
                plx_scale = 1000.0
            elif "pm_ra" in row and row["pm_ra"] != "":
                pm_ra = float(row["pm_ra"])
                pm_dec = float(row["pm_dec"])
                plx_scale = 1.0
            else:
                raise KeyError(
                    f"Row {row_idx} in {csv_path} missing proper motion columns "
                    f"(expected 'pmra'/'pmdec' or 'pm_ra'/'pm_dec'). Found: {list(row.keys())}"
                )

            if "parallax_arcsec" in row and row["parallax_arcsec"] != "":
                parallax: float | None = float(row["parallax_arcsec"])
            elif "parallax_mas" in row and row["parallax_mas"] != "":
                parallax = float(row["parallax_mas"]) / 1000.0
            elif "parallax" in row and row["parallax"] != "":
                parallax = float(row["parallax"]) / plx_scale
            else:
                parallax = None

            citation = row.get("citation", "")
            targets.append(
                BenchmarkTarget(
                    name=name,
                    ra=float(ra),
                    dec=float(dec),
                    pm_ra_arcsec_yr=pm_ra,
                    pm_dec_arcsec_yr=pm_dec,
                    parallax_arcsec=parallax,
                    citation=citation,
                )
            )

    has_stationary = any(t.is_stationary for t in targets)
    if not has_stationary:
        raise ValueError(
            "data/benchmarks.csv must include at least one stationary star "
            f"(literature proper motion < {STATIONARY_PM_THRESHOLD_ARCSEC_YR} arcsec/yr)."
        )
    return targets


def evaluate_criterion(
    target: BenchmarkTarget, fit: MotionFitResult
) -> tuple[str, float]:
    """Evaluate pre-committed AGREE / UNINFORMATIVE / DISAGREE criteria."""
    pm_lit = target.pm_total_arcsec_yr
    sigma_pm = fit.sigma_pm_total_arcsec_yr

    if target.is_stationary:
        # Stationary star criterion: measured proper motion must be consistent with zero (< 3 sigma)
        diff_sigma = fit.motion_significance
        status = "AGREE" if diff_sigma < 3.0 else "DISAGREE"
        return status, diff_sigma

    vec_diff = float(
        np.hypot(
            fit.pm_ra_arcsec_yr - target.pm_ra_arcsec_yr,
            fit.pm_dec_arcsec_yr - target.pm_dec_arcsec_yr,
        )
    )
    diff_sigma = vec_diff / sigma_pm if sigma_pm > 0.0 else float("inf")

    if sigma_pm >= 0.3 * pm_lit:
        return "UNINFORMATIVE", diff_sigma
    if diff_sigma < 3.0:
        return "AGREE", diff_sigma
    return "DISAGREE", diff_sigma


def investigate_systematics(
    target: BenchmarkTarget,
    fit: MotionFitResult,
    frames_used: Sequence[Frame],
    status: str,
) -> str:
    """Diagnose systematic effects when a measurement disagrees or has warnings."""
    notes: list[str] = list(fit.warnings)
    if status == "DISAGREE":
        if fit.n_epochs < 3 and not fit.parallax_fixed:
            notes.append(
                "SYSTEMATIC: <3 epochs without fixed parallax; parallax shift aliases into proper motion."
            )
        if fit.reduced_chi2 > 1.5:
            notes.append(
                f"SYSTEMATIC: High reduced chi2 ({fit.reduced_chi2:.2f}) indicates inter-frame WCS "
                "alignment residuals or unmodeled binary/photocentre motion."
            )
        if frames_used:
            waves = [f.wavelength for f in frames_used]
            wave_span = max(waves) - min(waves)
            if wave_span > 0.05:
                notes.append(
                    f"SYSTEMATIC: Wavelength span across group is {wave_span:.3f} um; "
                    "chromatic centroid shift or LVF gradient may bias positions."
                )
            if any(
                f.unreliable is not None and np.any(f.unreliable) for f in frames_used
            ):
                notes.append(
                    "SYSTEMATIC: Unreliable/saturated pixels present in cutout; "
                    "non-linear core may shift flux-weighted centroid."
                )
    return "; ".join(notes) if notes else "None"


def select_best_epoch_group(
    frames: Sequence[Frame],
) -> tuple[list[list[Frame]], list[Frame], str]:
    """Clean, group by detector/wavelength, split into epochs, and align the most populated group."""
    cleaned = [processing.clean_frame(f) for f in frames]
    groups = processing.group_frames(cleaned)

    best_epochs: list[list[Frame]] = []
    best_flat: list[Frame] = []
    best_wave_label = "N/A"

    group_iterable = groups.values() if isinstance(groups, dict) else groups

    for grp in group_iterable:
        epochs = processing.split_epochs(grp)
        if len(epochs) > len(best_epochs) or (
            len(epochs) == len(best_epochs)
            and sum(len(e) for e in epochs) > len(best_flat)
        ):
            aligned_flat = processing.align_frames(grp)
            best_epochs = processing.split_epochs(aligned_flat)
            best_flat = list(aligned_flat)
            waves = [f.wavelength for f in best_flat]
            det = best_flat[0].detector if best_flat else 0
            best_wave_label = f"D{det}:{min(waves):.3f}-{max(waves):.3f}"

    return best_epochs, best_flat, best_wave_label


def validate_target(
    target: BenchmarkTarget,
    fov_deg: float,
    snr_threshold: float,
    fix_parallax_on_degenerate: bool = True,
) -> ValidationRecord:
    """Fetch frames, measure motion, and evaluate benchmark criteria for one target."""
    raw_frames = data.get_frames(target.ra, target.dec, fov_deg)
    if not raw_frames:
        raise RuntimeError(f"no SPHEREx coverage at this position ({target.name})")

    epoch_groups, flat_frames, wave_label = select_best_epoch_group(raw_frames)
    if len(epoch_groups) < 2:
        raise RuntimeError(
            f"Target {target.name} has only {len(epoch_groups)} epoch(s) in best wavelength group."
        )

    fixed_plx = (
        target.parallax_arcsec
        if (len(epoch_groups) < 3 and fix_parallax_on_degenerate)
        else None
    )

    fit = measure_and_fit_epochs(
        epoch_frame_groups=epoch_groups,
        target_ra=target.ra,
        target_dec=target.dec,
        snr_threshold=snr_threshold,
        fixed_parallax_arcsec=fixed_plx,
    )

    status, diff_sigma = evaluate_criterion(target, fit)
    diagnostics = investigate_systematics(target, fit, flat_frames, status)
    ver_str = f"v{fit.version}" if fit.version is not None else "unknown"

    return ValidationRecord(
        name=target.name,
        status=status,
        pm_lit_arcsec_yr=target.pm_total_arcsec_yr,
        pm_meas_arcsec_yr=fit.pm_total_arcsec_yr,
        sigma_pm_arcsec_yr=fit.sigma_pm_total_arcsec_yr,
        pm_ra_lit_arcsec_yr=target.pm_ra_arcsec_yr,
        pm_ra_meas_arcsec_yr=fit.pm_ra_arcsec_yr,
        sigma_pm_ra_arcsec_yr=fit.sigma_pm_ra_arcsec_yr,
        pm_dec_lit_arcsec_yr=target.pm_dec_arcsec_yr,
        pm_dec_meas_arcsec_yr=fit.pm_dec_arcsec_yr,
        sigma_pm_dec_arcsec_yr=fit.sigma_pm_dec_arcsec_yr,
        parallax_lit_arcsec=target.parallax_arcsec,
        parallax_meas_arcsec=fit.parallax_arcsec,
        sigma_parallax_arcsec=fit.sigma_parallax_arcsec,
        diff_sigma=diff_sigma,
        reduced_chi2=fit.reduced_chi2,
        uncertainties_inflated=fit.uncertainties_inflated,
        n_epochs=fit.n_epochs,
        n_frames_used=fit.n_frames_used,
        n_frames_rejected=fit.n_frames_rejected,
        version=ver_str,
        wavelength_window_um=wave_label,
        citation=target.citation,
        diagnostics=diagnostics,
    )


def write_validation_outputs(
    records: Sequence[ValidationRecord],
    csv_out: Path,
    md_out: Path,
    command_str: str,
) -> None:
    """Write CSV and Markdown validation tables with command and version provenance."""
    csv_out.parent.mkdir(parents=True, exist_ok=True)
    md_out.parent.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    versions_used = sorted({r.version for r in records})

    fieldnames = [
        "name",
        "status",
        "pm_lit_arcsec_yr",
        "pm_meas_arcsec_yr",
        "sigma_pm_arcsec_yr",
        "pm_ra_lit_arcsec_yr",
        "pm_ra_meas_arcsec_yr",
        "sigma_pm_ra_arcsec_yr",
        "pm_dec_lit_arcsec_yr",
        "pm_dec_meas_arcsec_yr",
        "sigma_pm_dec_arcsec_yr",
        "parallax_lit_arcsec",
        "parallax_meas_arcsec",
        "sigma_parallax_arcsec",
        "diff_sigma",
        "reduced_chi2",
        "uncertainties_inflated",
        "n_epochs",
        "n_frames_used",
        "n_frames_rejected",
        "version",
        "wavelength_window_um",
        "citation",
        "diagnostics",
        "command",
        "timestamp_utc",
    ]

    with csv_out.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for r in records:
            writer.writerow(
                {
                    "name": r.name,
                    "status": r.status,
                    "pm_lit_arcsec_yr": f"{r.pm_lit_arcsec_yr:.5f}",
                    "pm_meas_arcsec_yr": f"{r.pm_meas_arcsec_yr:.5f}",
                    "sigma_pm_arcsec_yr": f"{r.sigma_pm_arcsec_yr:.5f}",
                    "pm_ra_lit_arcsec_yr": f"{r.pm_ra_lit_arcsec_yr:.5f}",
                    "pm_ra_meas_arcsec_yr": f"{r.pm_ra_meas_arcsec_yr:.5f}",
                    "sigma_pm_ra_arcsec_yr": f"{r.sigma_pm_ra_arcsec_yr:.5f}",
                    "pm_dec_lit_arcsec_yr": f"{r.pm_dec_lit_arcsec_yr:.5f}",
                    "pm_dec_meas_arcsec_yr": f"{r.pm_dec_meas_arcsec_yr:.5f}",
                    "sigma_pm_dec_arcsec_yr": f"{r.sigma_pm_dec_arcsec_yr:.5f}",
                    "parallax_lit_arcsec": (
                        f"{r.parallax_lit_arcsec:.5f}"
                        if r.parallax_lit_arcsec is not None
                        else ""
                    ),
                    "parallax_meas_arcsec": (
                        f"{r.parallax_meas_arcsec:.5f}"
                        if r.parallax_meas_arcsec is not None
                        else ""
                    ),
                    "sigma_parallax_arcsec": (
                        f"{r.sigma_parallax_arcsec:.5f}"
                        if r.sigma_parallax_arcsec is not None
                        else ""
                    ),
                    "diff_sigma": f"{r.diff_sigma:.2f}",
                    "reduced_chi2": f"{r.reduced_chi2:.2f}",
                    "uncertainties_inflated": str(r.uncertainties_inflated),
                    "n_epochs": r.n_epochs,
                    "n_frames_used": r.n_frames_used,
                    "n_frames_rejected": r.n_frames_rejected,
                    "version": r.version,
                    "wavelength_window_um": r.wavelength_window_um,
                    "citation": r.citation,
                    "diagnostics": r.diagnostics,
                    "command": command_str,
                    "timestamp_utc": timestamp,
                }
            )

    lines = [
        "# SPHEREx Proper Motion & Parallax Validation Report",
        "",
        f"- **Command:** `{command_str}`",
        f"- **Timestamp (UTC):** `{timestamp}`",
        f"- **Data versions used:** `{', '.join(versions_used)}`",
        "- **Pre-committed criteria:**",
        f"  - Stationary (`pm_lit < {STATIONARY_PM_THRESHOLD_ARCSEC_YR}\"/yr`): `AGREE` if `|pm| / sigma < 3`, else `DISAGREE`.",
        f"  - Moving (`pm_lit >= {STATIONARY_PM_THRESHOLD_ARCSEC_YR}\"/yr`): `UNINFORMATIVE` if `sigma >= 0.3 * pm_lit` (not a pass); "
        "`AGREE` if `|pm_vec - pm_lit_vec| < 3 * sigma` and `sigma < 0.3 * pm_lit`; `DISAGREE` otherwise.",
        "",
        "| Target | Status | PM Lit (\"/yr) | PM Meas +/- Sigma (\"/yr) | Diff (sigma) | Plx Meas +/- Sigma (\") | chi2_red | Epochs (Frames) | Version | Window (um) | Diagnostics |",
        "| :--- | :--- | ---: | ---: | ---: | ---: | ---: | ---: | :--- | :--- | :--- |",
    ]
    for r in records:
        plx_str = (
            f"{r.parallax_meas_arcsec:.4f} +/- {r.sigma_parallax_arcsec:.4f}"
            if r.parallax_meas_arcsec is not None
            and r.sigma_parallax_arcsec is not None
            else "N/A"
        )
        infl_tag = "*" if r.uncertainties_inflated else ""
        lines.append(
            f"| {r.name} | **{r.status}** | {r.pm_lit_arcsec_yr:.4f} | "
            f"{r.pm_meas_arcsec_yr:.4f} +/- {r.sigma_pm_arcsec_yr:.4f}{infl_tag} | "
            f"{r.diff_sigma:.2f} | {plx_str} | {r.reduced_chi2:.2f} | "
            f"{r.n_epochs} ({r.n_frames_used}/{r.n_frames_used + r.n_frames_rejected}) | "
            f"{r.version} | {r.wavelength_window_um} | {r.diagnostics} |"
        )

    md_out.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--benchmarks",
        type=Path,
        default=REPO_ROOT / "data" / "benchmarks.csv",
        help="Path to input benchmarks CSV.",
    )
    parser.add_argument(
        "--csv-out",
        type=Path,
        default=REPO_ROOT / "results" / "motion_validation.csv",
        help="Path to output validation CSV.",
    )
    parser.add_argument(
        "--md-out",
        type=Path,
        default=REPO_ROOT / "results" / "motion_validation.md",
        help="Path to output markdown summary.",
    )
    parser.add_argument(
        "--fov",
        type=float,
        default=0.05,
        help="Cutout field of view in degrees.",
    )
    parser.add_argument(
        "--snr-threshold",
        type=float,
        default=5.0,
        help="Minimum frame SNR threshold.",
    )
    parser.add_argument(
        "--target",
        type=str,
        default=None,
        help="Optional substring filter to run a single benchmark target.",
    )
    args = parser.parse_args(argv)

    cmd_str = "python " + " ".join(shlex.quote(a) for a in sys.argv)
    targets = load_benchmarks(args.benchmarks)
    if args.target:
        targets = [t for t in targets if args.target.lower() in t.name.lower()]
        if not targets:
            raise ValueError(f"No benchmark target matched --target {args.target!r}")

    records: list[ValidationRecord] = []

    for idx, target in enumerate(targets, start=1):
        print(
            f"[{idx}/{len(targets)}] Querying IRSA & fitting {target.name} "
            f"(RA={target.ra:.4f}, Dec={target.dec:.4f})...",
            flush=True,
        )
        try:
            rec = validate_target(
                target=target,
                fov_deg=args.fov,
                snr_threshold=args.snr_threshold,
            )
            print(
                f"  -> {rec.status}: PM={rec.pm_meas_arcsec_yr:.4f} +/- "
                f"{rec.sigma_pm_arcsec_yr:.4f} \"/yr (lit={rec.pm_lit_arcsec_yr:.4f}, "
                f"epochs={rec.n_epochs}, frames={rec.n_frames_used})",
                flush=True,
            )
            records.append(rec)
            write_validation_outputs(records, args.csv_out, args.md_out, cmd_str)
        except Exception as exc:
            print(f"  -> FAILED on {target.name}: {exc}", flush=True)

    if records:
        write_validation_outputs(records, args.csv_out, args.md_out, cmd_str)
        print(f"Wrote {len(records)} validation records to {args.csv_out} and {args.md_out}")
        return 0
    print("No targets succeeded.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())


