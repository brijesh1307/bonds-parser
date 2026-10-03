"""Parse-time benchmark over PDFs (default: the mock slips). Nothing is stored.

Usage:  python tools/benchmark.py [--rounds 10] [file.pdf ...]
Prints min / p50 / p95 / max per file and overall, against the 3 s p95 target.
"""

from __future__ import annotations

import argparse
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config import Settings  # noqa: E402
from app.engine.pipeline import run  # noqa: E402

TARGET_P95 = 3.0


def _p95(values: list[float]) -> float:
    return statistics.quantiles(values, n=20)[-1] if len(values) >= 2 else values[0]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdfs", nargs="*", type=Path)
    parser.add_argument("--rounds", type=int, default=10)
    args = parser.parse_args()
    pdfs = args.pdfs or sorted((ROOT / "samples" / "mock").glob("*.pdf"))
    settings = Settings.from_env().engine

    print(f"{'file':42} {'pages':>5} {'min':>7} {'p50':>7} {'p95':>7} {'max':>7}  status")
    every: list[float] = []
    for pdf in pdfs:
        data = pdf.read_bytes()
        result = run(data, file_name=pdf.name, sha256="0" * 64, es=settings)  # warm-up
        times = []
        for _ in range(args.rounds):
            start = time.perf_counter()
            run(data, file_name=pdf.name, sha256="0" * 64, es=settings)
            times.append(time.perf_counter() - start)
        every += times
        print(
            f"{pdf.name[:42]:42} {result.pages:>5} {min(times):7.3f} {statistics.median(times):7.3f} "
            f"{_p95(times):7.3f} {max(times):7.3f}  {result.status}"
        )
    p95 = _p95(every)
    print(
        f"\noverall: {len(every)} parses, p50 {statistics.median(every):.3f}s, p95 {p95:.3f}s "
        f"(target {TARGET_P95:.1f}s) -> {'OK' if p95 <= TARGET_P95 else 'OVER TARGET'}"
    )
    return 0 if p95 <= TARGET_P95 else 1


if __name__ == "__main__":
    sys.exit(main())
