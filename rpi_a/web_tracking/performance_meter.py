import time
import tracemalloc
import statistics
import psutil
import os
from contextlib import contextmanager
from functools import wraps
from dataclasses import dataclass, field
from typing import Callable, Any


@dataclass
class Sample:
    latency_ms: float
    cpu_percent: float
    ram_used_mb: float
    ram_peak_mb: float
    section: str = ""           # empty string means whole-function sample

class SectionMeter:
    """
    Context manager returned by PerformanceMeter.section().
    Records one Sample into the parent meter when the block exits.

    Usage:
        with meter.section("db query"):
            rows = db.execute(sql)
    """

    def __init__(self, parent: "PerformanceMeter", label: str) -> None:
        self._parent = parent
        self._label = label
        self._process = psutil.Process(os.getpid())

    def __enter__(self) -> "SectionMeter":
        self._process.cpu_percent(interval=None)   # prime CPU counter
        tracemalloc.start()
        self._start = time.perf_counter()
        return self

    def __exit__(self, *_) -> None:
        elapsed_ms = (time.perf_counter() - self._start) * 1000
        _, ram_peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()

        cpu = self._process.cpu_percent(interval=None)
        ram_rss_mb = self._process.memory_info().rss / 1024 / 1024

        self._parent._samples.append(Sample(
            latency_ms=elapsed_ms,
            cpu_percent=cpu,
            ram_used_mb=ram_rss_mb,
            ram_peak_mb=ram_peak / 1024 / 1024,
            section=self._label,
        ))


@dataclass
class PerformanceMeter:
    name: str
    _samples: list[Sample] = field(default_factory=list, repr=False)

    # ── measurement API ───────────────────────────────────────────────────────

    def measure(self, func: Callable, *args, **kwargs) -> Any:
        """Run a function once, record its performance, and return its result."""
        with self.section(""):
            result = func(*args, **kwargs)
        # fix the section label — section() records "" by default
        self._samples[-1].section = ""
        return result

    def measure_n(self, func: Callable, n: int = 10, *args, **kwargs) -> list[Any]:
        """Run a function n times and collect performance samples."""
        return [self.measure(func, *args, **kwargs) for _ in range(n)]

    def decorator(self, func: Callable) -> Callable:
        """Use as a decorator to auto-measure every call to a function."""
        @wraps(func)
        def wrapper(*args, **kwargs):
            return self.measure(func, *args, **kwargs)
        return wrapper

    def section(self, label: str) -> SectionMeter:
        """
        Context manager that measures a named block of code inside a function.

        Example:
            with meter.section("parse"):
                data = parse(raw)

            with meter.section("db write"):
                db.save(data)

            meter.report()                  # shows all sections separately
            meter.report(section="parse")   # filter to one section only
        """
        return SectionMeter(self, label)

    # ── statistics helpers ────────────────────────────────────────────────────

    @staticmethod
    def _p95(values: list[float]) -> float:
        if not values:
            return 0.0
        sorted_vals = sorted(values)
        idx = max(int(len(sorted_vals) * 0.95) - 1, 0)
        return sorted_vals[idx]

    def _stats(self, values: list[float]) -> dict:
        if not values:
            return {"avg": 0.0, "p95": 0.0, "min": 0.0, "max": 0.0}
        return {
            "avg": statistics.mean(values),
            "p95": self._p95(values),
            "min": min(values),
            "max": max(values),
        }

    # ── reporting ─────────────────────────────────────────────────────────────

    def _print_section(self, label: str, samples: list[Sample]) -> None:
        col = 36
        div = "─" * col
        display = label if label else "(whole function)"

        latency  = self._stats([s.latency_ms  for s in samples])
        cpu      = self._stats([s.cpu_percent  for s in samples])
        ram_used = self._stats([s.ram_used_mb  for s in samples])
        ram_peak = self._stats([s.ram_peak_mb  for s in samples])

        def row(lbl, avg, p95, unit=""):
            print(f"  {lbl:<18}  avg: {avg:>9.2f}{unit}   p95: {p95:>9.2f}{unit}")

        print(f"\n┌{div}┐")
        print(f"│  {self.name} › {display:<{col - len(self.name) - 4}}│")
        print(f"│  Samples: {len(samples):<{col - 11}}│")
        print(f"├{div}┤")
        row("Latency",    latency["avg"],  latency["p95"],  " ms")
        row("CPU usage",  cpu["avg"],      cpu["p95"],      " %")
        row("RAM (RSS)",  ram_used["avg"], ram_used["p95"], " MB")
        row("RAM (peak)", ram_peak["avg"], ram_peak["p95"], " MB")
        print(f"└{div}┘")

    def report(self, section: str | None = None) -> None:
        """
        Print avg and p95 for recorded samples.

        Args:
            section: if given, only report that named section.
                     If None, report every section (grouped separately).
        """
        if not self._samples:
            print(f"[{self.name}] No samples recorded yet.")
            return

        if section is not None:
            filtered = [s for s in self._samples if s.section == section]
            if not filtered:
                print(f"[{self.name}] No samples for section '{section}'.")
                return
            self._print_section(section, filtered)
        else:
            # group by section label and print each group
            seen: dict[str, list[Sample]] = {}
            for s in self._samples:
                seen.setdefault(s.section, []).append(s)
            for label, group in seen.items():
                self._print_section(label, group)
        print()

    def sections(self) -> list[str]:
        """Return a list of all recorded section labels."""
        seen = []
        for s in self._samples:
            if s.section not in seen:
                seen.append(s.section)
        return seen

    def clear(self) -> None:
        """Reset all recorded samples."""
        self._samples.clear()


# ── example usage ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import math

    meter = PerformanceMeter(name="pipeline")

    def process(n: int) -> list[float]:
        # measure individual stages inside the function
        with meter.section("compute"):
            result = [math.sqrt(i) for i in range(n)]

        with meter.section("sort"):
            result.sort(reverse=True)

        with meter.section("serialize"):
            _ = ",".join(f"{v:.4f}" for v in result)

        return result

    for _ in range(20):
        process(200_000)

    # report all sections together
    meter.report()

    # or zoom in on one section
    meter.report(section="compute")

    # --- whole-function decorator still works ---
    meter2 = PerformanceMeter(name="fetch_data")

    @meter2.decorator
    def fetch_data(size: int) -> list:
        return [i ** 2 for i in range(size)]

    for _ in range(15):
        fetch_data(100_000)

    meter2.report()
