"""Decode AWS BUFR in a separate process to isolate ecCodes native libraries."""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

from eccodes import CodesInternalError

from ai_weather_eval.preprocessing.ifs_bufr import decode_message, split_bufr_messages
from ai_weather_eval.preprocessing.ifs_cxml import GENESIS_ID


def decode_file(path: Path, source_file: str, init: datetime) -> tuple[list[dict], list[dict]]:
    messages, points = [], []
    for number, encoded in enumerate(split_bufr_messages(path.read_bytes()), start=1):
        msg, rows = decode_message(
            encoded, source_file=source_file, message_id=f"{source_file}#{number}",
        )
        if datetime.fromisoformat(msg["init_time"]) != init:
            raise ValueError("BUFR initialization disagrees with the requested cycle")
        if not GENESIS_ID.fullmatch(msg["storm_identifier"]):
            messages.append(msg)
            points.extend(rows)
    return messages, points


def main() -> None:
    output = {}
    for path, source_file, init in json.load(sys.stdin):
        try:
            messages, points = decode_file(Path(path), source_file, datetime.fromisoformat(init))
        except (ValueError, CodesInternalError) as exc:
            output[source_file] = {"error": f"{type(exc).__name__}: {exc}"}
        else:
            output[source_file] = {"messages": messages, "points": points}
    json.dump(output, sys.stdout)


if __name__ == "__main__":
    main()
