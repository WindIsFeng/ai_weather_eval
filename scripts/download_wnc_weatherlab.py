"""Download and inventory Weather Lab cyclone tracks for a fixed date range.

Run with: conda run --name ai-weather-eval python -u scripts/download_wnc_weatherlab.py
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


BASE_URL = "https://deepmind.google.com/science/weatherlab/download/cyclones"
PRODUCTS = ("ensemble", "ensemble_mean")
HOURS = (0, 6, 12, 18)


def cycles(start: date, end: date):
    day = start
    while day <= end:
        for hour in HOURS:
            yield datetime(day.year, day.month, day.day, hour)
        day += timedelta(days=1)


def fetch_one(model: str, product: str, init: datetime, root: Path) -> dict[str, object]:
    filename = f"{model}_{init:%Y_%m_%dT%H_00}_paired.csv"
    url = f"{BASE_URL}/{model}/{product}/paired/csv/{filename}"
    target = root / model / product / str(init.year) / filename
    target.parent.mkdir(parents=True, exist_ok=True)
    status = "existing" if target.is_file() and target.stat().st_size > 0 else "downloaded"
    error = ""
    if status == "downloaded":
        for attempt in range(4):
            temporary = target.with_name(f".{target.name}.part")
            try:
                with urlopen(Request(url, headers={"User-Agent": "ai-weather-eval/0.1"}), timeout=45) as response:
                    with temporary.open("wb") as stream:
                        while chunk := response.read(1 << 20):
                            stream.write(chunk)
                temporary.replace(target)
                break
            except HTTPError as exc:
                temporary.unlink(missing_ok=True)
                if exc.code == 404:
                    status, error = "missing", "HTTP 404"
                    break
                error = f"HTTP {exc.code}"
            except (URLError, TimeoutError, OSError) as exc:
                temporary.unlink(missing_ok=True)
                error = str(exc)[:200]
            if attempt == 3:
                status = "failed"
            else:
                time.sleep(2**attempt)
    if status in {"downloaded", "existing"}:
        digest = hashlib.sha256()
        with target.open("rb") as stream:
            while chunk := stream.read(1 << 20):
                digest.update(chunk)
        size = target.stat().st_size
        sha256 = digest.hexdigest()
        with target.open("rb") as stream:
            expected_header = stream.read(100).startswith(b"#")
        if size == 0 or not expected_header:
            status, error = "invalid", "Empty file or unexpected header"
    else:
        size, sha256 = 0, ""
    return {
        "model": model,
        "product": product,
        "init_time": init.isoformat() + "Z",
        "status": status,
        "bytes": size,
        "sha256": sha256,
        "path": str(target),
        "url": url,
        "error": error,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", type=date.fromisoformat, default=date(2023, 1, 1))
    parser.add_argument("--end", type=date.fromisoformat, default=date(2024, 12, 31))
    parser.add_argument("--model", default="FNV3P2")
    parser.add_argument("--root", type=Path, default=Path("data/raw/forecasts/wnc_weatherlab"))
    parser.add_argument("--manifest", type=Path, default=Path("outputs/wnc_2023_2024/download_manifest.csv"))
    parser.add_argument("--workers", type=int, default=16)
    args = parser.parse_args()
    if args.end < args.start or args.workers < 1:
        parser.error("invalid date range or worker count")
    jobs = [(product, init) for init in cycles(args.start, args.end) for product in PRODUCTS]
    results = []
    counts = {}
    started = time.monotonic()
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(fetch_one, args.model, product, init, args.root) for product, init in jobs]
        for future in as_completed(futures):
            item = future.result()
            results.append(item)
            counts[item["status"]] = counts.get(item["status"], 0) + 1
            if len(results) % 200 == 0 or len(results) == len(jobs):
                print(f"{len(results)}/{len(jobs)} files; {counts}; {time.monotonic() - started:.0f}s", flush=True)
    args.manifest.parent.mkdir(parents=True, exist_ok=True)
    with args.manifest.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(results[0]))
        writer.writeheader()
        writer.writerows(sorted(results, key=lambda item: (item["init_time"], item["product"])))
    print(f"Manifest: {args.manifest}", flush=True)


if __name__ == "__main__":
    main()
