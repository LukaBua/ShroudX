# AI-assisted: written with AI pair-programming assistant.
"""CLI to pre-fetch and cache SPHEREx cutouts from IRSA by target name or coordinates."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from astropy.coordinates import Angle, SkyCoord

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import data  # noqa: E402


def _resolve_position(
    *,
    target: str | None,
    ra: str | None,
    dec: str | None,
) -> tuple[float, float, list[str]]:
    """Resolve target name or parse RA/Dec into decimal degrees using coords.py if present."""
    warnings: list[str] = []
    if target is not None and (ra is not None or dec is not None):
        raise ValueError("Specify either --target OR both --ra and --dec, not both.")
    if target is None and (ra is None or dec is None):
        raise ValueError("Specify either --target OR both --ra and --dec.")

    try:
        import coords  # type: ignore[import-not-found]
    except ImportError:
        coords = None

    if target is not None:
        if coords is not None and hasattr(coords, "resolve_name"):
            resolved = coords.resolve_name(target)
            res_warns = list(getattr(resolved, "warnings", ()))
            return float(resolved.ra), float(resolved.dec), res_warns
        sc = SkyCoord.from_name(target)
        return float(sc.icrs.ra.deg), float(sc.icrs.dec.deg), warnings

    assert ra is not None and dec is not None
    if coords is not None and hasattr(coords, "parse_position"):
        ra_deg, dec_deg = coords.parse_position(ra, dec)
        return float(ra_deg), float(dec_deg), warnings

    ra_str = str(ra).strip()
    dec_str = str(dec).strip()
    try:
        ra_deg = float(ra_str)
    except ValueError:
        if any(c in ra_str.lower() for c in ("h", "d", "m", "s")):
            ra_deg = float(Angle(ra_str).deg)
        else:
            ra_deg = float(Angle(ra_str, unit="hourangle").deg)

    try:
        dec_deg = float(dec_str)
    except ValueError:
        if any(c in dec_str.lower() for c in ("d", "m", "s")):
            dec_deg = float(Angle(dec_str).deg)
        else:
            dec_deg = float(Angle(dec_str, unit="deg").deg)

    if not (0.0 <= ra_deg < 360.0):
        raise ValueError(f"RA must be in [0, 360) deg, got {ra_deg}")
    if not (-90.0 <= dec_deg <= 90.0):
        raise ValueError(f"Dec must be in [-90, 90] deg, got {dec_deg}")
    return ra_deg, dec_deg, warnings


def build_parser() -> argparse.ArgumentParser:
    """Create the argument parser for ``scripts/fetch_cutouts.py``."""
    parser = argparse.ArgumentParser(
        description="Fetch and cache SPHEREx cutouts from IRSA by target name or coordinates."
    )
    parser.add_argument(
        "--target",
        type=str,
        default=None,
        help="Target name to resolve (e.g. 'WISE 0855-0714' or 'Barnard star').",
    )
    parser.add_argument(
        "--ra",
        type=str,
        default=None,
        help="Right Ascension in decimal degrees or sexagesimal hours.",
    )
    parser.add_argument(
        "--dec",
        type=str,
        default=None,
        help="Declination in decimal degrees or sexagesimal degrees.",
    )
    parser.add_argument(
        "--fov",
        type=float,
        default=480.0,
        help="Cutout field of view in arcseconds (default: 480).",
    )
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=Path("cache"),
        help="Root cache directory (default: cache).",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=data.MAX_DOWNLOAD_WORKERS,
        help="Maximum parallel download workers (1 to 5, default: 5).",
    )
    parser.add_argument(
        "--retries",
        type=int,
        default=4,
        help="Retries per cutout download (default: 4).",
    )
    parser.add_argument(
        "--mode",
        type=str,
        default="live",
        choices=["live", "auto", "cache"],
        help="Data retrieval mode (default: live).",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the CLI cutout fetcher and return an exit status code."""
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        ra_deg, dec_deg, warnings = _resolve_position(
            target=args.target,
            ra=args.ra,
            dec=args.dec,
        )
    except Exception as exc:
        print(f"Error resolving position: {exc}", file=sys.stderr)
        return 2

    for w in warnings:
        print(f"Note: {w}")

    bucket = data.select_size_bucket(args.fov)
    key = data.cache_key_for(ra_deg, dec_deg, bucket)
    print(
        f"Fetching SPHEREx cutouts at RA={ra_deg:.5f} deg, Dec={dec_deg:+.5f} deg "
        f"(FOV={args.fov:.0f}\", cache bucket={bucket}\", key={key})..."
    )

    def _report_progress(done: int, total: int, uri: str, ok: bool) -> None:
        status = "OK" if ok else "FAILED"
        print(f"  [{done}/{total}] {status}: {uri}")

    try:
        frames = data.get_frames(
            ra_deg,
            dec_deg,
            args.fov,
            mode=args.mode,
            cache_root=args.cache_dir,
            max_workers=args.workers,
            retries=args.retries,
            progress_callback=_report_progress,
            use_memory_cache=False,
        )
    except data.DataError as exc:
        print(f"Error ({exc.code}): {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    detectors = sorted({f.detector for f in frames})
    versions = sorted({f.version for f in frames if f.version is not None})
    print(
        f"Cached {len(frames)} frames in {args.cache_dir / key} "
        f"(detectors={detectors}, versions={versions})."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
