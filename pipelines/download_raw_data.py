"""Downloads the real GTFS.DE feed and piebro/deutsche-bahn-data monthly
delay archive into data/raw/, per DATA_SPEC.md §3 step 1 and §4 step 1.

This is a manual/periodic script -- NOT called by app.py, data_loader.py,
or pipelines/build_dataset.py at request time. Run it yourself, on your own
schedule (DATA_SPEC.md suggests re-running the GTFS download roughly every
Fahrplanwechsel, i.e. quarterly; the delay archive whenever a new month is
published).

Why two rail feeds rather than gtfs.de's "complete" national one:
DATA_SPEC.md §3 step 1.

IF A DOWNLOAD 404s: re-check https://gtfs.de/de/feeds/ -- the site links each
feed through an intermediate page (de_fv / de_rv) to its current latest.zip,
so the direct URLs below can move. For the delay archive, browse
https://huggingface.co/datasets/piebro/deutsche-bahn-data to see which
months exist, then pass --months (DEFAULT_MONTHS only tracks one).

HOW TO RUN (see the very bottom of this file too):
    pip install -r requirements-dev.txt      # tqdm lives there, not in requirements.txt
    python -m pipelines.download_raw_data

Downloads only what's missing by default (skips a file that already exists
at its destination path); pass --force to re-download anyway. Large files
are streamed to a .part file and only renamed into place once the download
completes successfully, so an interrupted run never leaves a corrupt file
sitting at the final path.
"""

import argparse
from pathlib import Path

import requests

try:
    from tqdm import tqdm
except ImportError as exc:  # pragma: no cover - human-run script, not exercised by pytest
    raise SystemExit(
        "This script needs tqdm for its progress bar. Install it first:\n"
        "    pip install -r requirements-dev.txt"
    ) from exc

RAW_DATA_DIR = Path(__file__).parent.parent / "data" / "raw"

# Verified live against https://gtfs.de/de/feeds/ on 2026-08-23.
GTFS_FEEDS: dict[str, str] = {
    "fv": "https://download.gtfs.de/germany/fv_free/latest.zip",  # ICE / IC / EC
    "rv": "https://download.gtfs.de/germany/rv_free/latest.zip",  # RE / RB / S-Bahn
}

PIEBRO_BASE_URL = (
    "https://huggingface.co/datasets/piebro/deutsche-bahn-data"
    "/resolve/main/monthly_processed_data"
)
DEFAULT_MONTHS = ["2026-07"]  # most recent complete month as of 2026-08-23


def _download_with_progress(url: str, dest_path: Path, force: bool = False) -> Path:
    """Stream url to dest_path with a tqdm progress bar, skipping if it
    already exists (unless force=True). Downloads to a .part file first so
    an interrupted run can't leave a truncated file at dest_path."""
    if dest_path.exists() and not force:
        print(f"  already have {dest_path.name}, skipping (pass --force to re-download)")
        return dest_path

    dest_path.parent.mkdir(parents=True, exist_ok=True)
    part_path = dest_path.with_suffix(dest_path.suffix + ".part")

    with requests.get(url, stream=True, timeout=30) as response:
        response.raise_for_status()
        total_bytes = int(response.headers.get("content-length", 0))
        with (
            open(part_path, "wb") as f,
            tqdm(
                total=total_bytes,
                unit="B",
                unit_scale=True,
                unit_divisor=1024,
                desc=dest_path.name,
            ) as bar,
        ):
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                f.write(chunk)
                bar.update(len(chunk))

    part_path.replace(dest_path)
    return dest_path


def download_gtfs_feeds(
    feeds: list[str], out_dir: Path = RAW_DATA_DIR, force: bool = False
) -> list[Path]:
    """DATA_SPEC.md §3 step 1: fetch the current GTFS.DE zip(s)."""
    paths = []
    for feed in feeds:
        url = GTFS_FEEDS[feed]
        dest = out_dir / f"gtfs_{feed}_latest.zip"
        print(f"Downloading {feed} GTFS feed from {url}")
        paths.append(_download_with_progress(url, dest, force=force))
    return paths


def download_delay_months(
    months: list[str], out_dir: Path = RAW_DATA_DIR, force: bool = False
) -> list[Path]:
    """DATA_SPEC.md §4 step 1: fetch one or more monthly delay Parquet files."""
    paths = []
    for month in months:
        url = f"{PIEBRO_BASE_URL}/data-{month}.parquet"
        dest = out_dir / f"delays_{month}.parquet"
        print(f"Downloading {month} delay archive from {url}")
        paths.append(_download_with_progress(url, dest, force=force))
    return paths


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--feeds",
        default="fv,rv",
        help="Comma-separated GTFS.DE feed keys to download (choices: fv, rv). Default: fv,rv",
    )
    parser.add_argument(
        "--months",
        default=",".join(DEFAULT_MONTHS),
        help="Comma-separated YYYY-MM months of the piebro delay archive to download.",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=RAW_DATA_DIR,
        help=f"Destination directory. Default: {RAW_DATA_DIR}",
    )
    parser.add_argument(
        "--force", action="store_true", help="Re-download even if the file already exists."
    )
    args = parser.parse_args()

    feeds = [f.strip() for f in args.feeds.split(",") if f.strip()]
    months = [m.strip() for m in args.months.split(",") if m.strip()]

    downloaded = download_gtfs_feeds(feeds, args.out_dir, force=args.force)
    downloaded += download_delay_months(months, args.out_dir, force=args.force)

    print("\nDone. Downloaded files:")
    for path in downloaded:
        size_mb = path.stat().st_size / (1024 * 1024)
        print(f"  {path}  ({size_mb:.1f} MB)")


if __name__ == "__main__":
    main()

# To run this script from the project root:
#
#   pip install -r requirements-dev.txt
#   python -m pipelines.download_raw_data
#
# Options:
#   python -m pipelines.download_raw_data --feeds fv,rv --months 2026-06,2026-07
#   python -m pipelines.download_raw_data --out-dir data/raw --force
