"""Performance target (docs/10_testing_strategy.md §7): p95 parse time ≤ 3 s for 1-5 page text PDFs.

Runs every mock slip a few times through the full pipeline (extraction to validation).
Detailed numbers: `python tools/benchmark.py`.
"""

from __future__ import annotations

import statistics
import time

from app.config import Settings
from app.engine.pipeline import run
from tests.conftest import MOCK_DIR

P95_LIMIT_SECONDS = 3.0
ROUNDS = 3


def test_p95_parse_time_within_target() -> None:
    settings = Settings().engine
    timings = []
    for pdf in sorted(MOCK_DIR.glob("*.pdf")):
        data = pdf.read_bytes()
        run(data, file_name=pdf.name, sha256="0" * 64, es=settings)  # warm-up (imports, caches)
        for _ in range(ROUNDS):
            start = time.perf_counter()
            run(data, file_name=pdf.name, sha256="0" * 64, es=settings)
            timings.append(time.perf_counter() - start)
    p95 = statistics.quantiles(timings, n=20)[-1]
    assert p95 <= P95_LIMIT_SECONDS, f"p95 {p95:.3f}s over {len(timings)} parses"
