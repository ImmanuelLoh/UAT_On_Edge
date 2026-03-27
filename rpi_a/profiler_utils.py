import time
import threading
from collections import defaultdict, deque

class RollingProfiler:
    def __init__(self, max_samples=500):
        self.max_samples = max_samples
        self._lock = threading.Lock()
        self._samples = defaultdict(lambda: deque(maxlen=max_samples))
        self._counters = defaultdict(int)

    def record_ms(self, name: str, ms: float):
        with self._lock:
            self._samples[name].append(ms)
            self._counters[f"{name}.count"] += 1

    def incr(self, name: str, value: int = 1):
        with self._lock:
            self._counters[name] += value

    def snapshot(self) -> dict:
        with self._lock:
            out = {"timings_ms": {}, "counters": dict(self._counters)}

            for name, values in self._samples.items():
                if not values:
                    continue
                arr = list(values)
                arr_sorted = sorted(arr)
                n = len(arr_sorted)

                def percentile(p: float) -> float:
                    idx = min(int(p * (n - 1)), n - 1)
                    return round(arr_sorted[idx], 3)

                out["timings_ms"][name] = {
                    "count": n,
                    "avg": round(sum(arr) / n, 3),
                    "min": round(arr_sorted[0], 3),
                    "p50": percentile(0.50),
                    "p90": percentile(0.90),
                    "p95": percentile(0.95),
                    "max": round(arr_sorted[-1], 3),
                }

            return out


class Timer:
    def __init__(self, profiler: RollingProfiler, name: str):
        self.profiler = profiler
        self.name = name
        self.start = None

    def __enter__(self):
        self.start = time.perf_counter()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        elapsed_ms = (time.perf_counter() - self.start) * 1000.0
        self.profiler.record_ms(self.name, elapsed_ms)