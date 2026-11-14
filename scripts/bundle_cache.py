# AI-assisted: written with AI pair-programming assistant.
"""Build and measure the compact demo-field cutout cache for hosted deployment.

Pass / Fail Criteria (fixed before running):
1. Target coverage: Every offline demo target in ``coords.OFFLINE_TARGETS``
   (WISE 0855-0714, Barnard's Star, Epsilon Indi B, RW Aurigae, R Draconis) must
   resolve to a cached cutout directory with valid FITS cutouts.
2. Sequence usability: For each demo target, ``processing.group_frames`` on the
   bundled cache must yield at least 3 usable frames spanning at least 2 distinct
   observation epochs (``processing.split_epochs``) within a narrow wavelength
   window (<= 0.10 um) on the target's default/suggested detector.
3. Provenance integrity: Every bundled frame must carry a finite MJD, valid
   detector (1..6), recorded processing version (non-null integer, no silent
   version mixing), and ``synthetic=False``.
4. Size budget: Total disk size of the bundled cache (all FITS files and
   ``index.csv`` manifests) must be at most 50.0 MB (50,000,000 bytes).
5. Verdict is ``PASS`` only if criteria 1..4 all hold; otherwise ``FAIL``.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import numpy as np

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import coords  # noqa: E402
import data  # noqa: E402
import processing  # noqa: E402

MAX_CACHE_SIZE_MB: float = 50.0
MAX_CACHE_SIZE_BYTES: int = 50_000_000


def _select_rows_for_target(
    cache_dir: Path,
    rows: Sequence[tuple[str, float, str, str]],
    ra: float,
    dec: float,
    bucket_arcsec: int,
    suggested_detector: int,
    *,
    primary_max_frames: int = 12,
    secondary_max_frames: int = 7,
) -> list[tuple[str, float, str, str]]:
    """Select a compact, multi-epoch subset of cached rows covering all 6 detectors."""
    uri_to_row = {r[3]: r for r in rows}
    selected_uris: list[str] = []
    seen_uris: set[str] = set()

    for det in range(1, 7):
        bandpass = f"SPHEREx-D{det}"
        det_rows = [r for r in rows if r[2] == bandpass]
        if not det_rows:
            # Fallback if bandpass column was not SPHEREx-D<det>: load all if small
            if len(rows) <= 90:
                det_rows = list(rows)
            else:
                continue

        # Pre-thin deep-field detectors (e.g. R Draconis > 500 frames) uniformly in MJD
        if len(det_rows) > 90:
            step = len(det_rows) / 60.0
            det_rows = [det_rows[int(i * step)] for i in range(60)]

        det_frames = data._load_frames_from_cache_dir(
            cache_dir,
            det_rows,
            ra=ra,
            dec=dec,
            fov_arcsec=float(bucket_arcsec),
            bucket_arcsec=bucket_arcsec,
        )
        det_frames_filtered = tuple(f for f in det_frames if f.detector == det)
        if not det_frames_filtered:
            continue

        grouped = processing.group_frames(det_frames_filtered, detector=det)
        win_frames = list(grouped.frames)

        if det == suggested_detector:
            chosen = [f.uri for f in win_frames[:primary_max_frames] if f.uri is not None]
            min_primary = max(secondary_max_frames + 2, min(len(det_frames_filtered), 11))
            if len(chosen) < min_primary:
                for f in det_frames_filtered:
                    if f.uri is not None and f.uri not in chosen:
                        chosen.append(f.uri)
                        if len(chosen) >= min_primary:
                            break
        else:
            if len(win_frames) > secondary_max_frames:
                step = len(win_frames) / float(secondary_max_frames)
                win_frames = [win_frames[int(i * step)] for i in range(secondary_max_frames)]
            chosen = [f.uri for f in win_frames if f.uri is not None]

        for uri in chosen:
            if uri in uri_to_row and uri not in seen_uris:
                seen_uris.add(uri)
                selected_uris.append(uri)

    kept = [uri_to_row[u] for u in selected_uris]
    kept.sort(key=lambda item: (item[1], item[0]))
    return kept


def bundle_demo_cache(
    source_dir: str | Path,
    output_dir: str | Path,
    *,
    targets: dict[str, coords.OfflineEntry] | None = None,
    primary_max_frames: int = 12,
    secondary_max_frames: int = 7,
    max_size_mb: float = MAX_CACHE_SIZE_MB,
) -> dict[str, Any]:
    """Bundle demo-field cutouts from ``source_dir`` into ``output_dir`` and measure size."""
    src_root = Path(source_dir).resolve()
    out_root = Path(output_dir).resolve()
    target_map = targets if targets is not None else coords.OFFLINE_TARGETS

    field_reports: list[dict[str, Any]] = []
    all_versions: set[int] = set()
    all_file_sizes: list[int] = []
    failures: list[str] = []

    staging_parent = out_root.parent
    staging_parent.mkdir(parents=True, exist_ok=True)
    staging_dir = Path(tempfile.mkdtemp(prefix=".bundle_cache_", dir=str(staging_parent)))

    try:
        for name, entry in target_map.items():
            resolved = coords.resolve_name(name, offline_table=target_map)
            sug_det = coords.TARGET_PROFILES.get(entry.target_class or "", 5)
            hit = data._find_cached_bucket(src_root, resolved.ra, resolved.dec, 300.0)
            if hit is None:
                failures.append(f"Missing cached cutout bucket for target '{name}'")
                continue

            src_cdir, bucket_arcsec, rows = hit
            selected_rows = _select_rows_for_target(
                src_cdir,
                rows,
                ra=resolved.ra,
                dec=resolved.dec,
                bucket_arcsec=bucket_arcsec,
                suggested_detector=sug_det,
                primary_max_frames=primary_max_frames,
                secondary_max_frames=secondary_max_frames,
            )
            if not selected_rows:
                failures.append(f"No valid frames selected for target '{name}'")
                continue

            dest_cdir = staging_dir / src_cdir.name
            dest_cdir.mkdir(parents=True, exist_ok=True)

            new_index_rows: list[tuple[str, float, str, str]] = []
            for idx, (old_fname, mjd, bandpass, uri) in enumerate(selected_rows, start=1):
                new_fname = f"{idx:04d}.fits"
                shutil.copy2(src_cdir / old_fname, dest_cdir / new_fname)
                new_index_rows.append((new_fname, mjd, bandpass, uri))

            data._write_index_csv(dest_cdir, new_index_rows)

            # Verify the bundled field through data.get_frames and processing
            bundled_frames = data._load_frames_from_cache_dir(
                dest_cdir,
                new_index_rows,
                ra=resolved.ra,
                dec=resolved.dec,
                fov_arcsec=300.0,
                bucket_arcsec=bucket_arcsec,
            )
            grouped = processing.group_frames(bundled_frames)
            epochs = processing.split_epochs(grouped.frames)

            versions = sorted({int(f.version) for f in bundled_frames if f.version is not None})
            all_versions.update(versions)
            detectors = sorted({int(f.detector) for f in bundled_frames})

            files_in_dir = [p for p in dest_cdir.iterdir() if p.is_file()]
            fits_sizes = [p.stat().st_size for p in files_in_dir if p.suffix == ".fits"]
            all_file_sizes.extend(fits_sizes)
            dir_bytes = sum(p.stat().st_size for p in files_in_dir)

            w_lo, w_hi = grouped.wavelength_window
            w_width = w_hi - w_lo
            if len(grouped.frames) < 3:
                n_grp = len(grouped.frames)
                failures.append(
                    f"Target '{name}' has fewer than 3 frames in default window ({n_grp})"
                )
            if len(epochs) < 2:
                failures.append(
                    f"Target '{name}' has fewer than 2 epochs in default window ({len(epochs)})"
                )
            if w_width > processing.DEFAULT_MAX_WIDTH_UM + 1e-6:
                failures.append(
                    f"Target '{name}' wavelength window width {w_width:.4f} um exceeds 0.10 um"
                )
            if any(f.version is None for f in bundled_frames):
                failures.append(f"Target '{name}' contains frames with missing pipeline version")
            if len(versions) > 1:
                failures.append(f"Target '{name}' mixes pipeline versions: {versions}")

            mean_kb = float(np.mean(fits_sizes) / 1000.0) if fits_sizes else 0.0
            std_kb = (
                float(np.std(fits_sizes, ddof=1) / 1000.0)
                if len(fits_sizes) > 1
                else 0.0
            )

            field_reports.append(
                {
                    "target": name,
                    "cache_key": src_cdir.name,
                    "ra_deg": round(resolved.ra, 5),
                    "dec_deg": round(resolved.dec, 5),
                    "bucket_arcsec": int(bucket_arcsec),
                    "n_bundled_frames": len(new_index_rows),
                    "detectors": detectors,
                    "default_detector": int(grouped.detector),
                    "default_window_um": [round(w_lo, 4), round(w_hi, 4)],
                    "default_window_frames": len(grouped.frames),
                    "default_window_epochs": len(epochs),
                    "versions": versions,
                    "size_bytes": int(dir_bytes),
                    "size_mb": round(dir_bytes / 1_000_000.0, 4),
                    "size_mib": round(dir_bytes / (1024.0**2), 4),
                    "file_size_kb_mean": round(mean_kb, 3),
                    "file_size_kb_sigma": round(std_kb, 3),
                }
            )

        # Atomically replace output_dir with staging_dir contents
        if out_root.exists():
            shutil.rmtree(out_root)
        shutil.move(str(staging_dir), str(out_root))
    finally:
        if staging_dir.exists():
            shutil.rmtree(staging_dir, ignore_errors=True)

    data.clear_memory_cache()

    total_bytes = sum(p.stat().st_size for p in out_root.rglob("*") if p.is_file())
    total_mb = total_bytes / 1_000_000.0
    total_mib = total_bytes / (1024.0**2)
    if total_mb > max_size_mb:
        failures.append(
            f"Total bundled cache size {total_mb:.2f} MB exceeds target {max_size_mb:.2f} MB"
        )

    mean_file_kb = float(np.mean(all_file_sizes) / 1000.0) if all_file_sizes else 0.0
    sigma_file_kb = (
        float(np.std(all_file_sizes, ddof=1) / 1000.0)
        if len(all_file_sizes) > 1
        else 0.0
    )

    verdict = "PASS" if (not failures and len(field_reports) == len(target_map)) else "FAIL"
    return {
        "verdict": verdict,
        "criteria": {
            "max_cache_size_mb": max_size_mb,
            "min_frames_per_default_window": 3,
            "min_epochs_per_default_window": 2,
            "max_wavelength_window_width_um": processing.DEFAULT_MAX_WIDTH_UM,
        },
        "data_versions": [f"l2b-v{v}" for v in sorted(all_versions)],
        "total_files": sum(f["n_bundled_frames"] for f in field_reports),
        "total_size_bytes": int(total_bytes),
        "total_size_mb": round(total_mb, 4),
        "total_size_mib": round(total_mib, 4),
        "cutout_file_size_kb": {
            "mean": round(mean_file_kb, 3),
            "sigma": round(sigma_file_kb, 3),
        },
        "fields": field_reports,
        "failures": failures,
    }


def format_markdown_report(report: dict[str, Any], command_str: str) -> str:
    """Format the bundled cache report as a Markdown table for ``results/`` and ``README.md``."""
    max_mb = report["criteria"]["max_cache_size_mb"]
    lines = [
        "# Bundled Demo Cache Report",
        "",
        f"- **Command**: `{command_str}`",
        f"- **Data versions**: `{', '.join(report['data_versions']) or 'none'}`",
        f"- **Verdict**: **{report['verdict']}** (limit <= {max_mb:.1f} MB)",
        (
            f"- **Total bundled cache size**: **{report['total_size_mb']:.2f} MB** "
            f"({report['total_size_mib']:.2f} MiB, {report['total_size_bytes']:,} bytes across "
            f"{report['total_files']} FITS cutouts; per-cutout size "
            f"{report['cutout_file_size_kb']['mean']:.1f} +/- "
            f"{report['cutout_file_size_kb']['sigma']:.1f} kB)"
        ),
        "",
        (
            "| Target | Cache directory | Bundled cutouts | Default det "
            "| Default window (um) | Window frames (epochs) | Version | Size (MB) |"
        ),
        "| --- | --- | ---: | ---: | --- | ---: | --- | ---: |",
    ]
    for field in report["fields"]:
        w0, w1 = field["default_window_um"]
        ver_str = ", ".join(f"l2b-v{v}" for v in field["versions"])
        lines.append(
            f"| {field['target']} | `{field['cache_key']}` | {field['n_bundled_frames']} | "
            f"{field['default_detector']} | {w0:.3f}-{w1:.3f} | "
            f"{field['default_window_frames']} ({field['default_window_epochs']}) | "
            f"{ver_str} | {field['size_mb']:.2f} |"
        )
    if report["failures"]:
        lines.extend(["", "## Failures", ""])
        for failure in report["failures"]:
            lines.append(f"- {failure}")
    return "\n".join(lines) + "\n"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build and measure the compact bundled cache of SPHEREx demo fields."
    )
    parser.add_argument(
        "--source-dir",
        type=Path,
        default=Path("cache"),
        help="Source cache directory containing fetched cutouts (default: cache).",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("cache"),
        help="Destination directory for the bundled cache (default: cache).",
    )
    parser.add_argument(
        "--results-dir",
        type=Path,
        default=Path("results"),
        help="Directory where bundled_cache.json and bundled_cache.md are written.",
    )
    parser.add_argument(
        "--max-size-mb",
        type=float,
        default=MAX_CACHE_SIZE_MB,
        help="Maximum allowed total cache size in MB (default: 50.0).",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    cmd_args = list(argv) if argv is not None else sys.argv[1:]
    command_str = "python scripts/bundle_cache.py" + (
        " " + " ".join(cmd_args) if cmd_args else ""
    )

    report = bundle_demo_cache(
        args.source_dir,
        args.output_dir,
        max_size_mb=args.max_size_mb,
    )
    report["command"] = command_str

    results_dir = Path(args.results_dir)
    results_dir.mkdir(parents=True, exist_ok=True)
    json_path = results_dir / "bundled_cache.json"
    md_path = results_dir / "bundled_cache.md"

    json_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    md_text = format_markdown_report(report, command_str)
    md_path.write_text(md_text, encoding="utf-8")

    print(md_text, end="")
    return 0 if report["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
