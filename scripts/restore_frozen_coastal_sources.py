"""Restore the frozen IBTrACS extract and fixed GSHHG shoreline for the catalog."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import shutil
from pathlib import Path
from zipfile import ZipFile


ROOT = Path(__file__).resolve().parents[1]
IBTRACS_GZIP = ROOT / "data/frozen_sources/ibtracs.since1980.2026-09-22.study_range.csv.gz"
IBTRACS_SHA256 = "290b57e7d353b5a7db6bab9ea818144f4cb835c58e17a7d4a5bd7ca0fe65d028"
GSHHG_SHA256 = "8dbbe7e071e77e9e75f2d639239099ebca8d5c16d6a07df8169729d49f15cf41"
GSHHG_URL = "https://ftp.soest.hawaii.edu/gshhg/gshhg-shp-2.3.7.zip"
GSHHG_MEMBERS = tuple(
    f"GSHHS_shp/i/GSHHS_i_L1.{suffix}" for suffix in ("shp", "shx", "dbf", "prj")
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def restore(data_root: Path) -> None:
    ibtracs_path = data_root / "raw/ibtracs/ibtracs.since1980.2026-09-22.study_range.csv"
    if ibtracs_path.exists():
        if sha256(ibtracs_path) != IBTRACS_SHA256:
            raise ValueError(f"Existing IBTrACS input has the wrong SHA-256: {ibtracs_path}")
    else:
        ibtracs_path.parent.mkdir(parents=True, exist_ok=True)
        with gzip.open(IBTRACS_GZIP, "rb") as source, ibtracs_path.open("wb") as target:
            shutil.copyfileobj(source, target)
        if sha256(ibtracs_path) != IBTRACS_SHA256:
            ibtracs_path.unlink()
            raise ValueError("Restored IBTrACS input failed SHA-256 verification")

    archive = data_root / "raw/coastline/gshhg/2.3.7/gshhg-shp-2.3.7.zip"
    if not archive.is_file():
        raise FileNotFoundError(
            f"Place the GSHHG 2.3.7 archive at {archive}. Source: {GSHHG_URL}"
        )
    if sha256(archive) != GSHHG_SHA256:
        raise ValueError(f"GSHHG archive has the wrong SHA-256: {archive}")
    with ZipFile(archive) as source:
        for member in GSHHG_MEMBERS:
            target = data_root / "raw/coastline/gshhg/2.3.7" / member
            content = source.read(member)
            if target.exists():
                if target.read_bytes() != content:
                    raise ValueError(f"Existing GSHHG shapefile component differs: {target}")
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
    print(f"Frozen sources restored and verified under {data_root}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, default=ROOT / "data")
    args = parser.parse_args()
    restore(args.data_root.resolve())


if __name__ == "__main__":
    main()
