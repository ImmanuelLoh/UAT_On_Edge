"""
tracker_bridge_benchmark.py
PASO Benchmark - E2E system profiling.

Instruments the full tracker_bridge pipeline:
  - Face pipeline update() latency          (per-frame, tight loop)
  - State -> MQTT lag                       (face updated -> publish, ~0-500ms by design)
  - MQTT publish() latency                  (per publish call)
  - HTTP POST latency to Flask              (face / mouse / UAT endpoints, optional)

Flask HTTP metrics are skipped gracefully if Flask is not running.

Run (from repo root):
    python3 rpi_a/benchmarks/tracker_bridge_benchmark.py <RECEIVER_IP> <LABEL>

    e.g.  python3 rpi_a/benchmarks/tracker_bridge_benchmark.py 192.168.1.100 1

Measurement window: 60s (after calibration). Ctrl+C prints summary early.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import copy
import json
import signal
import threading
import time

import numpy as np
import requests

from rpi_a.sensors.face_sensor import FaceSensor
from rpi_a.sensors.mouse_tracker import MouseTracker
from rpi_a.transmission.MQTTClient import MQTTClient

# ---------------------------------------------------------------------------
# ARGS
# ---------------------------------------------------------------------------
if len(sys.argv) < 3:
    print("Usage: python3 rpi_a/benchmarks/tracker_bridge_benchmark.py <RECEIVER_IP> <LABEL>")
    sys.exit(1)

RECEIVER_IP = sys.argv[1]
LABEL = int(sys.argv[2])

FLASK_URL = "http://127.0.0.1:5000"   # set to None to skip HTTP POST timing
MEASURE = 60                          # seconds of data collection post-calibration

# ---------------------------------------------------------------------------
# SHARED STATE (mirrors tracker_bridge)
# ---------------------------------------------------------------------------
state_lock = threading.Lock()
latest_face_result = None
latest_face_time = None   # perf_counter timestamp of last face update

latest_state = {
    "timestamp": None,
    "browser": {"task": "benchmark", "correct_click": 0, "wrong_click": 0},
    "mouse":   {"idle_time": 0.0, "mouse_status": "unknown",
                 "interval_clicks_per_second": 0.0,
                 "overall_clicks_per_second": 0.0, "top_quadrant": "unknown"},
    "face":    {"face_detected": False, "frustration_score": 0.0,
                 "attention_score": 0.0, "emotion": "N/A", "direction": "N/A",
                 "gaze_quadrant": "NO_FACE", "blink_rate": 0.0, "avg_ear": 0.0},
    "llm":     {"llm_activated": False, "last_role": None, "last_message": ""},
}


def get_state_snapshot():
    with state_lock:
        return copy.deepcopy(latest_state)


# ---------------------------------------------------------------------------
# METRIC LISTS
# ---------------------------------------------------------------------------
face_update_list = []   # face_sensor.update() latency (ms)
mqtt_pub_list = []   # mqtt_client.publish() latency (ms)
mqtt_lag_list = []   # face update -> MQTT publish lag (ms)
http_face_list = []   # HTTP POST /api/face_event (ms)
http_mouse_list = []   # HTTP POST /api/mouse_event (ms)
http_uat_list = []   # HTTP POST /api/browser_event (ms)

metrics_lock = threading.Lock()

shutdown_event = threading.Event()
measuring_event = threading.Event()   # set when 60s window starts


def _append(lst, val):
    if measuring_event.is_set():
        with metrics_lock:
            lst.append(val)


# ---------------------------------------------------------------------------
# FACE LOOP
# ---------------------------------------------------------------------------
def face_loop(face_sensor: FaceSensor):
    global latest_face_result, latest_face_time

    print("[Benchmark] Face loop started.")
    while not shutdown_event.is_set():
        t0 = time.perf_counter()
        result = face_sensor.update()
        t1 = time.perf_counter()
        face_ms = (t1 - t0) * 1000

        _append(face_update_list, face_ms)

        if result is None:
            continue

        with state_lock:
            if result.get("face_detected"):
                latest_state["face"].update({
                    "face_detected": True,
                    "frustration_score": result.get("frustration_score", 0.0),
                    "attention_score": result.get("attention_score", 0.0),
                    "emotion": result.get("emotion", "N/A"),
                    "direction": result.get("direction", "N/A"),
                    "gaze_quadrant": result.get("gaze_quadrant", "UNCALIBRATED"),
                    "blink_rate": result.get("blink_rate", 0.0),
                    "avg_ear": result.get("avg_ear", 0.0),
                })
            else:
                latest_state["face"]["face_detected"] = False
            latest_state["timestamp"] = time.time()
            latest_face_time = t1

        # HTTP POST to Flask (optional)
        if FLASK_URL and result.get("face_detected"):
            payload = {
                "type": "face_state",
                **{k: result.get(k) for k in (
                    "face_detected", "frustration_score", "attention_score",
                    "emotion", "direction", "gaze_quadrant", "blink_rate", "avg_ear"
                )},
            }
            try:
                t0 = time.perf_counter()
                requests.post(f"{FLASK_URL}/api/face_event", json=payload, timeout=(0.2, 1.5))
                _append(http_face_list, (time.perf_counter() - t0) * 1000)
            except Exception:
                pass


# ---------------------------------------------------------------------------
# MOUSE LOOP
# ---------------------------------------------------------------------------
def mouse_loop(mouse_tracker: MouseTracker):
    while not shutdown_event.is_set():
        try:
            metrics = mouse_tracker.generate_metrics()
            with state_lock:
                latest_state["mouse"].update({
                    "idle_time": metrics.get("idle_time", 0.0),
                    "mouse_status": metrics.get("mouse_status", "unknown"),
                    "interval_clicks_per_second": metrics.get("interval_clicks_per_second", 0.0),
                    "overall_clicks_per_second": metrics.get("overall_clicks_per_second", 0.0),
                    "top_quadrant": metrics.get("top_quadrant", "unknown"),
                })
                latest_state["timestamp"] = time.time()

            if FLASK_URL:
                try:
                    t0 = time.perf_counter()
                    requests.post(f"{FLASK_URL}/api/mouse_event", json=metrics, timeout=0.5)
                    _append(http_mouse_list, (time.perf_counter() - t0) * 1000)
                except Exception:
                    pass

        except Exception as e:
            print("[Mouse Loop Error]", e)

        time.sleep(1)


# ---------------------------------------------------------------------------
# MQTT LOOP  (mirrors tracker_bridge 0.5s publish cadence)
# ---------------------------------------------------------------------------
def mqtt_loop(mqtt_client: MQTTClient):
    global latest_face_time

    print("[Benchmark] MQTT loop started.")
    while not shutdown_event.is_set():
        time.sleep(0.5)

        snapshot = get_state_snapshot()
        face_snap_time = latest_face_time   # timestamp of most recent face update

        payload = mqtt_client.build_payload(LABEL, snapshot)

        t0 = time.perf_counter()
        mqtt_client.publish(payload)
        t1 = time.perf_counter()
        pub_ms = (t1 - t0) * 1000

        _append(mqtt_pub_list, pub_ms)

        if face_snap_time is not None:
            lag_ms = (t1 - face_snap_time) * 1000
            _append(mqtt_lag_list, lag_ms)


# ---------------------------------------------------------------------------
# UAT HTTP LOOP  (mirrors uat_bridge_loop 1s cadence)
# ---------------------------------------------------------------------------
def uat_http_loop():
    dummy_payload = {
        "type": "task_state",
        "task": "benchmark",
        "correct_click": 0,
        "wrong_click": 0,
    }
    while not shutdown_event.is_set():
        if FLASK_URL:
            try:
                t0 = time.perf_counter()
                requests.post(f"{FLASK_URL}/api/browser_event", json=dummy_payload, timeout=0.5)
                _append(http_uat_list, (time.perf_counter() - t0) * 1000)
            except Exception:
                pass
        time.sleep(1)


# ---------------------------------------------------------------------------
# SUMMARY
# ---------------------------------------------------------------------------
def print_summary():
    print("\n" + "=" * 65)
    print("  BENCHMARK SUMMARY — E2E (tracker_bridge)")
    print(f"  MQTT broker : {RECEIVER_IP}  label={LABEL}")
    print(f"  Flask       : {FLASK_URL or 'skipped'}")
    print("=" * 65)

    all_metrics = [
        ("Face pipeline (update)", face_update_list),
        ("State -> MQTT lag", mqtt_lag_list),
        ("MQTT publish", mqtt_pub_list),
        ("HTTP POST (face→Flask)", http_face_list),
        ("HTTP POST (mouse→Flask)", http_mouse_list),
        ("HTTP POST (UAT→Flask)", http_uat_list),
    ]

    header = f"{'Metric':<30} {'Avg':>7} {'P50':>7} {'P95':>7} {'Max':>7} {'N':>6}"
    print(header)
    print("-" * len(header))

    for name, data in all_metrics:
        with metrics_lock:
            arr = np.array(data)
        if len(arr) == 0:
            print(f"{name:<30} {'N/A':>7}")
            continue
        print(
            f"{name:<30} {np.mean(arr):>7.2f} {np.median(arr):>7.2f} "
            f"{np.percentile(arr, 95):>7.2f} {np.max(arr):>7.2f} {len(arr):>6}"
        )

    print()
    print("NOTES:")
    print("  State -> MQTT lag is bounded by the 0.5s publish cadence (by design).")
    print("  HTTP POST N/A = Flask was not running or timed out.")
    print("  Face update N includes frames with no face detected.")


# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------
if __name__ == "__main__":

    # Handle Ctrl+C — print summary then exit
    def _sigint(sig, frame):
        print("\n[Benchmark] Interrupted - collecting summary...")
        shutdown_event.set()

    signal.signal(signal.SIGINT, _sigint)

    # --- Calibration ---
    import subprocess
    try:
        out = subprocess.check_output("xrandr | grep '*' | awk '{print $1}'", shell=True).decode().strip().split("\n")[0]
        screen_w, screen_h = map(int, out.split("x"))
    except Exception:
        screen_w, screen_h = 1920, 1080

    face_sensor = FaceSensor(screen_w, screen_h, debug=True)
    face_sensor.calibrate()

    # --- MQTT ---
    print(f"[Benchmark] Connecting to MQTT broker at {RECEIVER_IP}...")
    mqtt_client = MQTTClient(broker_ip=RECEIVER_IP, label=LABEL)
    time.sleep(1)   # allow on_connect to fire

    # --- Mouse ---
    mouse_tracker = MouseTracker(idle_threshold=5, interval=1)
    threading.Thread(target=mouse_tracker.start, daemon=True).start()

    # --- Start threads ---
    threading.Thread(target=face_loop, args=(face_sensor,),   daemon=True).start()
    threading.Thread(target=mouse_loop, args=(mouse_tracker,),  daemon=True).start()
    threading.Thread(target=mqtt_loop, args=(mqtt_client,),    daemon=True).start()
    threading.Thread(target=uat_http_loop, daemon=True).start()

    # --- Warmup then measure ---
    print(f"[Benchmark] Warming up (5s)...")
    time.sleep(5)

    print(f"[Benchmark] Measuring for {MEASURE}s... (Ctrl+C to stop early)")
    measuring_event.set()

    deadline = time.time() + MEASURE
    while time.time() < deadline and not shutdown_event.is_set():
        time.sleep(0.5)

    shutdown_event.set()
    time.sleep(0.5)   # let threads flush last metrics

    face_sensor.stop()
    print_summary()
