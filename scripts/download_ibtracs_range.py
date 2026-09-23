"""Fetch a contiguous, year-covering byte range from the official IBTrACS CSV.

The subset remains an extract of a mutable NOAA file; the metadata JSON records
the exact source revision and byte interval. Run in the ai-weather-eval Conda env.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.request import Request, urlopen


URL = (
    "https://www.ncei.noaa.gov/data/"
    "international-best-track-archive-for-climate-stewardship-ibtracs/"
    "v04r01/access/csv/ibtracs.since1980.list.v04r01.csv"
)


def get_range(start: int, end: int, etag: str) -> bytes:
    request = Request(URL, headers={"Range": f"bytes={start}-{end}", "If-Range": etag})
    for attempt in range(4):
        try:
            with urlopen(request, timeout=120) as response:
                content_range = response.headers.get("Content-Range", "")
                if response.status != 206 or not content_range.startswith(f"bytes {start}-{end}/"):
                    raise RuntimeError(f"Unexpected range response: {response.status} {content_range}")
                data = response.read()
            if len(data) != end - start + 1:
                raise RuntimeError(f"Incomplete range {start}-{end}: {len(data)} bytes")
            return data
        except Exception:
            if attempt == 3:
                raise
            time.sleep(2**attempt)
    raise RuntimeError("unreachable")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start-byte", type=int, default=129_000_000)
    parser.add_argument("--end-byte", type=int, default=141_999_999)
    parser.add_argument("--chunk-bytes", type=int, default=500_000)
    parser.add_argument("--workers", type=int, default=12)
    parser.add_argument("--output", type=Path, default=Path("data/raw/ibtracs/ibtracs_2023_2024_range.csv"))
    args = parser.parse_args()
    with urlopen(Request(URL, method="HEAD"), timeout=30) as response:
        metadata = {
            "source_url": URL,
            "source_last_modified": response.headers.get("Last-Modified"),
            "source_etag": response.headers["ETag"],
            "source_content_length": int(response.headers["Content-Length"]),
            "range_start": args.start_byte,
            "range_end": args.end_byte,
        }
    if args.start_byte < 0 or args.end_byte >= metadata["source_content_length"]:
        parser.error("range outside source file")
    header_bytes = get_range(0, 8191, metadata["source_etag"])
    header = b"\n".join(header_bytes.split(b"\n")[:2]) + b"\n"
    spans = [
        (start, min(start + args.chunk_bytes - 1, args.end_byte))
        for start in range(args.start_byte, args.end_byte + 1, args.chunk_bytes)
    ]
    chunks = {}
    started = time.monotonic()
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(get_range, start, end, metadata["source_etag"]): start for start, end in spans}
        for future in as_completed(futures):
            start = futures[future]
            chunks[start] = future.result()
            if len(chunks) % 5 == 0 or len(chunks) == len(spans):
                print(f"IBTrACS chunks {len(chunks)}/{len(spans)}; {time.monotonic() - started:.0f}s", flush=True)
    body = b"".join(chunks[start] for start, _ in spans)
    body = body.split(b"\n", 1)[1]
    body = body.rsplit(b"\n", 1)[0] + b"\n"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(header + body)
    metadata["extract_sha256"] = hashlib.sha256(header + body).hexdigest()
    metadata["extract_bytes"] = len(header + body)
    metadata["extract_rows"] = body.count(b"\n")
    args.output.with_suffix(".json").write_text(json.dumps(metadata, indent=2) + "\n")
    print(f"Extract: {args.output}; {metadata['extract_rows']} rows; {metadata['extract_bytes']} bytes", flush=True)


if __name__ == "__main__":
    main()
