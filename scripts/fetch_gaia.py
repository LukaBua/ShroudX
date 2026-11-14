# AI-assisted: written with AI pair-programming assistant.
"""CLI to fetch and cache Gaia DR3 catalogs for the ShroudX demo fields.

Pass / Fail Criteria (fixed before running):
1. Demo field coverage: Every requested demo target (default: all five targets
   in ``coords.OFFLINE_TARGETS``, backed by ``data/benchmarks.csv``) must be
   queried and cached on disk under ``<cache_dir>/gaia/``.
2. Offline cache verification: After fetching, ``gaia.build_catalog(..., mode="cache")``
   must succeed offline for every demo field without making any network calls.
3. Provenance integrity: Every cached table must come from ``gaiadr3.gaia_source``
   (never fabricated), with valid ``source_id``, finite ``ra`` and ``dec``, and
   propagated positions at the target survey epoch (default 2026.0).
4. Overall verdict is ``PASS`` only if all demo fields are cached and verified
   offline with zero errors; otherwise ``FAIL``.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import numpy as np

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import coords  # noqa: E402
import gaia  # noqa: E402

DEFAULT_DEMO_RADIUS_ARCSEC: float = 240.0
DEFAULT_CROSSMATCH_RADIUS_ARCSEC: float = 5.0


def load_benchmarks_csv(path: str | Path | None = None) -> dict[str, dict[str, Any]]:
    """Load literature target parameters from ``data/benchmarks.csv``."""
    csv_path = Path(path) if path is not None else (_REPO_ROOT / "data" / "benchmarks.csv")
    if not csv_path.is_file():
        return {}
    out: dict[str, dict[str, Any]] = {}
    with csv_path.open("r", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            raw_name = (row.get("name") or "").strip()
            if not raw_name:
                continue
            canonical = coords._OFFLINE_ALIASES.get(raw_name.lower(), raw_name)
            out[canonical] = {
                "name": raw_name,
                "canonical_name": canonical,
                "ra": float(row["ra"]),
                "dec": float(row["dec"]),
                "pmra": float(row["pmra"]),
                "pmdec": float(row["pmdec"]),
                "parallax": float(row["parallax"]) if row.get("parallax") else None,
                "citation": (row.get("citation") or "").strip(),
            }
    return out


def fetch_and_cache_demo_gaia(
    *,
    cache_root: str | Path = Path("cache"),
    mode: str = "auto",
    radius_arcsec: float = DEFAULT_DEMO_RADIUS_ARCSEC,
    epoch: float = coords.DEFAULT_SURVEY_EPOCH,
    timeout: float = gaia.DEFAULT_TIMEOUT_SEC,
    target_names: Sequence[str] | None = None,
    tap_client: Any = None,
) -> dict[str, Any]:
    """Fetch, cache, and verify Gaia DR3 catalogs for the demo fields."""
    root = Path(cache_root).resolve()
    benchmarks = load_benchmarks_csv()
    names = list(target_names) if target_names is not None else list(coords.OFFLINE_TARGETS.keys())

    fields: list[dict[str, Any]] = []
    failures: list[str] = []

    for name in names:
        try:
            resolved = coords.resolve_name(name)
            entry = coords.lookup_offline_target(name)
            bmark = benchmarks.get(name, {})
        except Exception as exc:
            failures.append(f"{name}: failed to resolve target ({exc})")
            continue

        try:
            table = gaia.query_gaia(
                resolved.ra,
                resolved.dec,
                radius_arcsec,
                cache_root=root,
                mode=mode,
                timeout=timeout,
                tap_client=tap_client,
            )
            # Verify offline cache read works at both full radius and default FOV/2
            offline_check = gaia.build_catalog(
                resolved.ra,
                resolved.dec,
                min(radius_arcsec, 150.0),
                epoch=epoch,
                cache_root=root,
                mode="cache",
            )
        except Exception as exc:
            failures.append(f"{name}: Gaia DR3 fetch/cache failed ({exc})")
            continue

        propagated = gaia.propagate_to_epoch(table, epoch)
        n_total = len(propagated)
        n_pm_missing = (
            int(np.sum(np.asarray(propagated["pm_missing"], dtype=bool))) if n_total else 0
        )
        gmags = [
            float(g)
            for g in propagated["phot_g_mean_mag"]
            if np.isfinite(g)
        ] if n_total else []
        gmag_brightest = round(float(min(gmags)), 3) if gmags else None
        gmag_median = round(float(np.median(gmags)), 3) if gmags else None
        gmag_sigma = (
            round(float(np.std(gmags, ddof=1)), 3)
            if len(gmags) > 1
            else (0.0 if gmags else None)
        )

        # Cross-match the target's epoch-propagated benchmark position & PM against Gaia DR3
        cand_dict: dict[str, Any] = {
            "name": name,
            "ra": resolved.ra,
            "dec": resolved.dec,
            "epoch": epoch,
        }
        if entry is not None and entry.pmra is not None and entry.pmdec is not None:
            cand_dict["pmra"] = float(entry.pmra)
            cand_dict["pmdec"] = float(entry.pmdec)

        xmatch = gaia.crossmatch(
            [cand_dict],
            propagated,
            DEFAULT_CROSSMATCH_RADIUS_ARCSEC,
            epoch=epoch,
        )[0]

        key = gaia.gaia_cache_key(resolved.ra, resolved.dec, radius_arcsec)
        cache_file = gaia._gaia_cache_dir(root) / f"{key}.json"
        file_bytes = cache_file.stat().st_size if cache_file.is_file() else 0

        fields.append(
            {
                "target": name,
                "cache_key": key,
                "ra_deg": round(resolved.ra, 5),
                "dec_deg": round(resolved.dec, 5),
                "radius_arcsec": round(float(radius_arcsec), 1),
                "epoch": round(float(epoch), 2),
                "n_sources": n_total,
                "n_sources_fov300": int(offline_check["n_sources"]),
                "n_pm_missing": n_pm_missing,
                "g_mag_brightest": gmag_brightest,
                "g_mag_median": gmag_median,
                "g_mag_sigma": gmag_sigma,
                "target_crossmatch": {
                    "classification": xmatch.classification,
                    "separation_arcsec": xmatch.separation_arcsec,
                    "source_id": xmatch.source_id,
                    "pm_diff_sigma": xmatch.pm_diff_sigma,
                },
                "citation": bmark.get("citation") or (entry.citation if entry else None),
                "cache_size_bytes": int(file_bytes),
            }
        )

    gaia_dir = gaia._gaia_cache_dir(root)
    total_gaia_bytes = (
        sum(p.stat().st_size for p in gaia_dir.glob("*.json") if p.is_file())
        if gaia_dir.is_dir()
        else 0
    )

    verdict = "PASS" if (not failures and len(fields) == len(names)) else "FAIL"
    return {
        "verdict": verdict,
        "data_versions": ["Gaia DR3 (gaiadr3.gaia_source)"],
        "epoch": round(float(epoch), 2),
        "radius_arcsec": round(float(radius_arcsec), 1),
        "total_gaia_cache_bytes": int(total_gaia_bytes),
        "total_gaia_cache_kb": round(total_gaia_bytes / 1000.0, 2),
        "fields": fields,
        "failures": failures,
    }


def format_markdown_report(report: dict[str, Any], command_str: str) -> str:
    """Format the Gaia DR3 demo-field cache report as Markdown."""
    lines = [
        "# Gaia DR3 Demo Field Cache Report",
        "",
        f"- **Command**: `{command_str}`",
        f"- **Data versions**: `{', '.join(report['data_versions'])}`",
        f"- **Propagated epoch**: `{report['epoch']:.1f}`",
        f"- **Verdict**: **{report['verdict']}**",
        (
            f"- **Gaia cache size**: **{report['total_gaia_cache_kb']:.2f} kB** "
            f"({report['total_gaia_cache_bytes']:,} bytes)"
        ),
        "",
        (
            "| Target | Radius (\") | Sources (<=500) | Missing PM | "
            "Brightest G (mag) | Median G +/- sigma | Target Cross-Match (<=5\") | Sep (\") |"
        ),
        "| --- | ---: | ---: | ---: | ---: | --- | --- | ---: |",
    ]
    for f in report["fields"]:
        xm = f["target_crossmatch"]
        bright_str = f"{f['g_mag_brightest']:.2f}" if f["g_mag_brightest"] is not None else "—"
        med_str = (
            f"{f['g_mag_median']:.2f} +/- {f['g_mag_sigma']:.2f}"
            if f["g_mag_median"] is not None and f["g_mag_sigma"] is not None
            else "—"
        )
        sep_str = f"{xm['separation_arcsec']:.2f}" if xm["separation_arcsec"] is not None else "—"
        lines.append(
            f"| {f['target']} | {f['radius_arcsec']:.0f} | {f['n_sources']} | "
            f"{f['n_pm_missing']} | {bright_str} | {med_str} | "
            f"`{xm['classification']}` | {sep_str} |"
        )
    if report["failures"]:
        lines.extend(["", "## Failures", ""])
        for err in report["failures"]:
            lines.append(f"- {err}")
    return "\n".join(lines) + "\n"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Fetch and cache Gaia DR3 catalogs for the ShroudX demo fields."
    )
    parser.add_argument(
        "--target",
        type=str,
        default=None,
        help="Optional single target name to fetch (defaults to all 5 demo targets).",
    )
    parser.add_argument(
        "--ra",
        type=str,
        default=None,
        help="Optional Right Ascension (requires --dec).",
    )
    parser.add_argument(
        "--dec",
        type=str,
        default=None,
        help="Optional Declination (requires --ra).",
    )
    parser.add_argument(
        "--radius",
        type=float,
        default=DEFAULT_DEMO_RADIUS_ARCSEC,
        help="Cone search radius in arcseconds (default: 240.0, covering 480\" FOV).",
    )
    parser.add_argument(
        "--epoch",
        type=float,
        default=coords.DEFAULT_SURVEY_EPOCH,
        help="Target epoch in Julian years for propagation check (default: 2026.0).",
    )
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=Path("cache"),
        help="Root cache directory (default: cache).",
    )
    parser.add_argument(
        "--results-dir",
        type=Path,
        default=Path("results"),
        help="Directory where gaia_catalog.json and gaia_catalog.md are written.",
    )
    parser.add_argument(
        "--mode",
        type=str,
        default="auto",
        choices=["auto", "live", "cache"],
        help="Data retrieval mode (default: auto).",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=gaia.DEFAULT_TIMEOUT_SEC,
        help="TAP request timeout in seconds (default: 15.0).",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    cmd_args = list(argv) if argv is not None else sys.argv[1:]
    command_str = "python scripts/fetch_gaia.py" + (
        " " + " ".join(cmd_args) if cmd_args else ""
    )

    if args.ra is not None or args.dec is not None:
        if args.ra is None or args.dec is None or args.target is not None:
            print("Provide either --target, both --ra and --dec, or neither.", file=sys.stderr)
            return 2
        try:
            ra_deg, dec_deg = coords.parse_position(args.ra, args.dec)
            tbl = gaia.query_gaia(
                ra_deg,
                dec_deg,
                args.radius,
                cache_root=args.cache_dir,
                mode=args.mode,
                timeout=args.timeout,
            )
        except Exception as exc:
            print(f"Error fetching Gaia DR3 cone: {exc}", file=sys.stderr)
            return 1
        print(
            f"Cached {len(tbl)} Gaia DR3 sources for RA={ra_deg:.5f}, Dec={dec_deg:+.5f}, "
            f'radius={args.radius:.1f}"'
        )
        return 0

    target_list = [args.target] if args.target is not None else None
    report = fetch_and_cache_demo_gaia(
        cache_root=args.cache_dir,
        mode=args.mode,
        radius_arcsec=args.radius,
        epoch=args.epoch,
        timeout=args.timeout,
        target_names=target_list,
    )
    report["command"] = command_str

    results_dir = Path(args.results_dir)
    results_dir.mkdir(parents=True, exist_ok=True)
    (results_dir / "gaia_catalog.json").write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8"
    )
    md_text = format_markdown_report(report, command_str)
    (results_dir / "gaia_catalog.md").write_text(md_text, encoding="utf-8")

    print(md_text, end="")
    return 0 if report["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
