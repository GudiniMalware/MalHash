"""Bounded result ordering and worker management for batch hashing."""

from __future__ import annotations

import os
from collections import deque
from collections.abc import Callable, Iterable, Iterator
from concurrent.futures import Future, ThreadPoolExecutor
from threading import Event

from malhash.algorithms import ALGORITHMS, DEFAULT_ALGORITHMS

from .fileinfo import METADATA_FIELDS
from .hashing import hash_target, select
from .models import FileResult, FileTarget
from .scanner import ScanOptions, scan_paths

MAX_WORKERS = 64


def process_paths(
    paths: Iterable[str | os.PathLike[str]],
    *,
    algorithms: Iterable[str] = DEFAULT_ALGORITHMS,
    metadata: Iterable[str] = (),
    scan_options: ScanOptions | None = None,
    workers: int = 1,
    cancel: Event | None = None,
    on_start: Callable[[FileTarget], None] | None = None,
) -> Iterator[FileResult]:
    """Stream results in discovery order, prefetching at most twice the worker count."""
    if not 1 <= workers <= MAX_WORKERS:
        raise ValueError(f"workers must be between 1 and {MAX_WORKERS}")
    active_algorithms = select(algorithms, ALGORITHMS)
    active_fields = select(metadata, METADATA_FIELDS)
    cancellation = cancel if cancel is not None else Event()
    targets = scan_paths(paths, scan_options, cancel=cancellation)

    def run(target: FileTarget) -> FileResult:
        if on_start is not None and not cancellation.is_set():
            on_start(target)
        return hash_target(target, active_algorithms, active_fields, cancellation)

    futures: deque[Future[FileResult] | FileResult] = deque()
    pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="malhash")
    finished = False
    try:
        for entry in targets:
            futures.append(entry if isinstance(entry, FileResult) else pool.submit(run, entry))
            if len(futures) >= 2 * workers:
                head = futures.popleft()
                yield head if isinstance(head, FileResult) else head.result()
        while futures:
            head = futures.popleft()
            yield head if isinstance(head, FileResult) else head.result()
        finished = True
    finally:
        if not finished:
            cancellation.set()
        pool.shutdown(wait=True, cancel_futures=True)
