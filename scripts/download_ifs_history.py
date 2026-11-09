"""Download the pinned AWS BUFR and NCAR CXML objects for missing IFS cycles."""

from __future__ import annotations

import argparse
import csv
import fcntl
import hashlib
import os
import re
import subprocess
import tempfile
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from xml.etree import ElementTree as ET

from ai_weather_eval.preprocessing.ifs_bufr import split_bufr_messages
from ai_weather_eval.preprocessing.ifs_cxml import read_cxml

PLAN = Path("data/manifests/ifs_historical_tracks_files_2026-11-10.csv")
RAW = Path("raw/baselines/ifs/historical_tracks")
INTERIM = Path("interim/ifs_historical_tracks")
SOURCES = {
    "aws": (
        "https://ecmwf-forecasts.s3.amazonaws.com",
        "https://ecmwf-forecasts.s3.eu-central-1.amazonaws.com",
        "https://d2zvc0wgha4k2l.cloudfront.net",
    ),
    "ncar": ("https://data.gdex.ucar.edu/d330003",),
}
INVENTORY_FIELDS = [
    "source", "init_time", "object_key", "source_file", "download_status",
    "download_url", "byte_range", "size_bytes", "sha256", "error",
]


def raw_path(source: str, init: datetime, key: str) -> Path:
    """Validate the request and return its immutable path under the data root."""
    stamp = init.strftime("%Y%m%d%H%M%S")
    if source == "aws":
        stream = "oper" if init.hour in (0, 12) else "scda"
        pattern = (
            rf"{stamp[:8]}/{stamp[8:10]}z/(?:0p4-beta|0p4|ifs/0p25)/{stream}/"
            rf"{stamp}-\d+h-{stream}-tf\.bufr"
        )
    elif source == "ncar":
        pattern = (
            rf"ecmf/{init.year}/{stamp[:8]}/z_tigge_c_ecmf_{stamp}_ifs_glob_prod_all_glo\.xml"
        )
    else:
        raise ValueError(f"Unknown archive source: {source}")
    if init.utcoffset() is None or init.utcoffset().total_seconds() != 0 or not re.fullmatch(
        pattern, key
    ):
        raise ValueError(f"Invalid object key for {source} at {init}: {key}")
    return RAW / source / key


def validate_file(source: str, file: Path, *, prefix: bool = False) -> None:
    if source == "aws":
        list(split_bufr_messages(file.read_bytes()))
    elif read_cxml(file, prefix=prefix).tag != "cxml":
        raise ValueError("NCAR response is not CXML")


def download(
    source: str, key: str, temporary: Path, timeout: int, byte_range: str, prefix: bool,
) -> tuple[str, str, str]:
    """Return the URL, acquired byte range (CXML prefixes only), and any failure."""
    if source == "aws":
        first, *rest = SOURCES[source]
        attempts = [(first, "0-65535"), (first, ""), *((host, "") for host in rest)]
    else:
        sizes = (byte_range, byte_range) if byte_range else ("0-262143", "0-524287", "0-1048575")
        attempts = [(SOURCES[source][0], span) for span in (sizes if prefix else ("", "", ""))]
    errors = []
    for index, (host, span) in enumerate(attempts):
        url = f"{host}/{key}"
        cmd = [
            "curl", "-fsSL", "--proto", "=https", "--proto-redir", "=https",
            "--connect-timeout", "10", "--max-time", str(timeout), "--max-filesize", "67108864",
            "--write-out", "%{http_code}\\n%header{content-range}", "--output", str(temporary),
        ]
        if span:
            cmd.extend(("--range", span))
        elif source == "ncar" and temporary.stat().st_size:
            cmd.extend(("--continue-at", "-"))
        result = subprocess.run([*cmd, url], capture_output=True, text=True)
        status, _, header = result.stdout.partition("\n")
        size = temporary.stat().st_size
        try:
            if prefix:
                if status != "206" or not header.startswith("bytes 0-"):
                    raise ValueError(
                        f"CXML range response had HTTP {status}, curl {result.returncode}"
                    )
                actual = f"0-{size - 1}"
                if byte_range and actual != byte_range:
                    raise ValueError("Incomplete pinned CXML byte range")
                validate_file(source, temporary, prefix=True)
                return url, actual, ""
            if result.returncode == 0:
                if span and status == "206":
                    expected = f"bytes 0-{size - 1}/{size}"
                    if header.strip() != expected:
                        raise ValueError("AWS range did not cover the full object")
                validate_file(source, temporary)
                return url, "", ""
        except (ValueError, ET.ParseError) as exc:
            errors.append(f"{host}: {exc}")
        else:
            errors.append(f"{host}: HTTP {status or 'unavailable'}, curl {result.returncode}")
        if prefix or source == "aws" or result.returncode not in (18, 28, 56):
            temporary.write_bytes(b"")
        if index + 1 < len(attempts):
            time.sleep(2 ** (index + 1))
    return "", "", "; ".join(errors)


def fetch(request: dict[str, str], root: Path, timeout: int) -> dict[str, object]:
    source, key = request["source"], request["object_key"]
    init = datetime.fromisoformat(request["init_time"])
    relative = raw_path(source, init, key)
    status, url, error = "existing", request.get("download_url", ""), ""
    expected = request.get("sha256", "")
    byte_range = request.get("byte_range", "")
    prefix = source == "ncar" and bool(
        byte_range or (not expected and not (root / relative).is_file())
    )
    if prefix:
        relative = relative.with_suffix(".xml.prefix")
    target = root / relative
    if not target.is_file():
        target.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=target.parent, prefix=f".{target.name}.") as stream:
            temporary = Path(stream.name)
            url, byte_range, error = download(source, key, temporary, timeout, byte_range, prefix)
            if url:
                if expected and hashlib.sha256(temporary.read_bytes()).hexdigest() != expected:
                    raise ValueError(f"Downloaded data disagree with the pinned checksum: {target}")
                try:
                    os.link(temporary, target)
                    status = "downloaded"
                except FileExistsError:
                    if target.read_bytes() != temporary.read_bytes():
                        raise ValueError(
                            f"Concurrent download has different content: {target}"
                        ) from None
            else:
                status = "failed"
    digest, size = "", 0
    if target.is_file():
        validate_file(source, target, prefix=prefix)
        digest = hashlib.sha256(target.read_bytes()).hexdigest()
        size = target.stat().st_size
        if expected and digest != expected:
            raise ValueError(f"Local data disagree with the pinned checksum: {target}")
        if prefix:
            byte_range = f"0-{size - 1}"
    return {
        "source": source, "init_time": init.isoformat(), "object_key": key,
        "source_file": relative.as_posix(), "download_status": status, "download_url": url,
        "byte_range": byte_range, "size_bytes": size, "sha256": digest, "error": error,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", choices=SOURCES, required=True)
    parser.add_argument("--data-root", type=Path,
                        default=Path(os.environ.get("AI_WEATHER_EVAL_DATA", "data")))
    parser.add_argument("--plan", type=Path, default=PLAN)
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--timeout", type=int, help="Transfer timeout in seconds per attempt")
    args = parser.parse_args()
    timeout = args.timeout or (12 if args.source == "aws" else 45)
    if args.workers < 1 or timeout < 1:
        parser.error("workers and timeout must be positive")

    inventory = args.data_root / INTERIM / f"{args.source}_downloads.csv"
    inventory.parent.mkdir(parents=True, exist_ok=True)
    with inventory.with_suffix(".lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            parser.error(f"another {args.source} download is already running")
        fetch_plan(args.plan, args.source, args.data_root, inventory, args.workers, timeout)


def fetch_plan(
    plan_path: Path, source: str, root: Path, inventory: Path, workers: int, timeout: int,
) -> None:
    previous = {}
    if inventory.is_file():
        with inventory.open(newline="") as stream:
            previous = {r["object_key"]: r for r in csv.DictReader(stream)}
    with plan_path.open(newline="") as stream:
        plan = [r for r in csv.DictReader(stream) if r["source"] == source]
    if not plan or len({r["object_key"] for r in plan}) != len(plan):
        raise ValueError("The selected source must have a non-empty plan with unique object keys")
    records = {}
    for row in plan:
        key = row["object_key"]
        known = previous.get(key, {})
        if known:
            records[key] = known
        for field in ("sha256", "download_url", "byte_range"):
            row[field] = row.get(field) or known.get(field, "")
    plan.sort(key=lambda row: not bool(row["sha256"]))

    counts: Counter = Counter()
    with ThreadPoolExecutor(workers) as pool:
        futures = [pool.submit(fetch, row, root, timeout) for row in plan]
        for completed, future in enumerate(as_completed(futures), start=1):
            item = future.result()
            records[item["object_key"]] = item
            write_inventory(inventory, records)
            counts[item["download_status"]] += 1
            if completed == len(plan) or completed % 5 == 0:
                print(f"{source} {completed}/{len(plan)} {dict(counts)}", flush=True)
    print(inventory, flush=True)


def write_inventory(path: Path, records: dict) -> None:
    # Preserve earlier successes and expose only complete CSV snapshots to the audit.
    with tempfile.NamedTemporaryFile("w", dir=path.parent, newline="", delete=False) as stream:
        temporary = Path(stream.name)
        writer = csv.DictWriter(stream, fieldnames=INVENTORY_FIELDS)
        writer.writeheader()
        writer.writerows(records[key] for key in sorted(records))
    temporary.replace(path)


if __name__ == "__main__":
    main()
