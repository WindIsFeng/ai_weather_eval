"""Small Linux process-tree RSS sampler; no monitoring dependency is required."""

from __future__ import annotations

import os
from pathlib import Path
from threading import Event, Thread


def _tree_rss_kib(pid: int) -> int:
    """Include spawned workers and the multiprocessing resource tracker."""
    pending = [pid]
    seen: set[int] = set()
    total = 0
    while pending:
        current = pending.pop()
        if current in seen:
            continue
        seen.add(current)
        try:
            folder = Path("/proc") / str(current)
            for line in (folder / "status").read_text().splitlines():
                if line.startswith("VmRSS:"):
                    total += int(line.split()[1])
                    break
            children = folder / "task" / str(current) / "children"
            pending.extend(int(value) for value in children.read_text().split())
        except (OSError, ValueError):
            # A worker can finish between enumerating it and reading its status.
            continue
    return total


class ProcessMemorySampler:
    """Sample aggregate resident memory every 0.1 s, including child processes.

    This is sampled RSS, not an instantaneous or unique physical-memory peak:
    shared pages can be counted more than once. Linux /proc is required; other
    hosts return None rather than claiming a measured process-tree peak.
    """

    def __init__(self, interval_seconds: float = 0.1):
        self.interval_seconds = interval_seconds
        self.peak_kib = 0
        self.available = Path("/proc/self/status").is_file()
        self._stop = Event()
        self._thread: Thread | None = None

    def _sample(self):
        self.peak_kib = max(self.peak_kib, _tree_rss_kib(os.getpid()))

    def _run(self):
        while not self._stop.wait(self.interval_seconds):
            self._sample()

    def __enter__(self):
        if self.available:
            self._sample()
            self._thread = Thread(target=self._run, name="tracker-memory", daemon=True)
            self._thread.start()
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self._stop.set()
        if self._thread is not None:
            self._thread.join()
        if self.available:
            self._sample()

    @property
    def peak_mib(self) -> float | None:
        return round(self.peak_kib / 1024.0, 3) if self.available else None
