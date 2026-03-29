import threading
import time
import requests
import subprocess

# MQTT
import copy
import json
import sys
import statistics
from pathlib import Path
from transmission.MQTTClient import MQTTClient
from transmission.VideoStreamClient import VideoStreamClient
from transmission.ProcessSupervisor import ProcessSupervisor

from sensors.face_sensor import FaceSensor
from sensors.uat_monitor import UATMonitor, UATTask
from sensors.web_tracker import WebTracker
from sensors.mouse_tracker import MouseTracker
from datetime import datetime, timezone, timedelta

# MQTT transmission config
RECEIVER_IP = sys.argv[1]
LABEL = int(sys.argv[2])


# ================================
# Session Recorder
class SessionRecorder:
    """Buffers every MQTT snapshot and builds an end-of-session summary."""

    def __init__(self, label: int):
        self._lock = threading.Lock()
        self._snapshots: list[dict] = []
        self._start_time: float = time.time()
        self.label = label

    def record(self, snapshot: dict):
        with self._lock:
            self._snapshots.append(copy.deepcopy(snapshot))

    def _safe_avg(self, values):
        clean = [v for v in values if isinstance(v, (int, float))]
        return round(statistics.mean(clean), 4) if clean else 0.0

    def _safe_max(self, values):
        clean = [v for v in values if isinstance(v, (int, float))]
        return round(max(clean), 4) if clean else 0.0
    
    def _get_frustration(self):
        with self._lock:
            snapshots = list(self._snapshots)[-60:]

        count = sum(1 for s in snapshots if s["face"]["emotion"] == "FRUSTRATED")
        return count >= 20
    
    def build_summary(self, session_id: str) -> dict:
        with self._lock:
            snapshots = list(self._snapshots)

        end_time = time.time()
        duration = round(end_time - self._start_time, 2)

        # ── aggregates ──────────────────────────────────────────────
        face_snaps   = [s["face"]   for s in snapshots if s.get("face")]
        mouse_snaps  = [s["mouse"]  for s in snapshots if s.get("mouse")]
        browser_snaps = [s["browser"] for s in snapshots if s.get("browser")]

        # Face aggregates
        detected = [f for f in face_snaps if f.get("face_detected")]
        face_agg = {
            "snapshots_with_face": len(detected),
            "avg_frustration_score": self._safe_avg([f["frustration_score"] for f in detected]),
            "peak_frustration_score": self._safe_max([f["frustration_score"] for f in detected]),
            "avg_attention_score": self._safe_avg([f["attention_score"] for f in detected]),
            "avg_blink_rate": self._safe_avg([f["blink_rate"] for f in detected]),
            "emotion_counts": {},
            "gaze_quadrant_counts": {},
        }
        for f in detected:
            emo = f.get("emotion", "N/A")
            face_agg["emotion_counts"][emo] = face_agg["emotion_counts"].get(emo, 0) + 1
            gq = f.get("gaze_quadrant", "unknown")
            face_agg["gaze_quadrant_counts"][gq] = face_agg["gaze_quadrant_counts"].get(gq, 0) + 1

        # Mouse aggregates
        mouse_agg = {
            "avg_idle_time": self._safe_avg([m["idle_time"] for m in mouse_snaps]),
            "peak_idle_time": self._safe_max([m["idle_time"] for m in mouse_snaps]),
            "avg_interval_clicks_per_second": self._safe_avg([m["interval_clicks_per_second"] for m in mouse_snaps]),
            "avg_overall_clicks_per_second": self._safe_avg([m["overall_clicks_per_second"] for m in mouse_snaps]),
            "top_quadrant_counts": {},
            "mouse_status_counts": {},
        }
        for m in mouse_snaps:
            tq = m.get("top_quadrant", "unknown")
            mouse_agg["top_quadrant_counts"][tq] = mouse_agg["top_quadrant_counts"].get(tq, 0) + 1
            ms = m.get("mouse_status", "unknown")
            mouse_agg["mouse_status_counts"][ms] = mouse_agg["mouse_status_counts"].get(ms, 0) + 1

        # Browser aggregates (Sum of Max per Task)
        task_maxima = {} # Stores { "task_name": {"correct": X, "wrong": Y} }

        for b in browser_snaps:
            task_name = b.get("task", "unknown")
            
            # Initialize task entry if not seen yet
            if task_name not in task_maxima:
                task_maxima[task_name] = {"correct": 0, "wrong": 0}
            
            # Update with the highest value seen for this specific task
            task_maxima[task_name]["correct"] = max(task_maxima[task_name]["correct"], b.get("correct_click", 0))
            task_maxima[task_name]["wrong"] = max(task_maxima[task_name]["wrong"], b.get("wrong_click", 0))

        # Sum the peaks from every task
        total_correct = sum(data["correct"] for data in task_maxima.values())
        total_wrong = sum(data["wrong"] for data in task_maxima.values())

        browser_agg = {
            "total_wrong_clicks": total_wrong,
            "total_correct_clicks": total_correct,
            "task_breakdown": task_maxima 
        }

        # LLM aggregates
        llm_activation_by_task = {}

        for snap in snapshots:
            task = snap.get("browser", {}).get("task", "unknown")
            llm  = snap.get("llm", {})

            if llm.get("llm_activated"):
                llm_activation_by_task[task] = True

        llm_agg = {
            "activation_by_task": llm_activation_by_task,  # e.g. {"Number Selection": True}
        }

        return {
            "meta": {
                "label": self.label,
                "session_active": False,
                "start_time": round(self._start_time, 2),
                "end_time": round(end_time, 2),
                "session_id": session_id,
                "duration_seconds": duration,
                "total_snapshots": len(snapshots),
            },
            "aggregates": {
                "face": face_agg,
                "mouse": mouse_agg,
                "browser": browser_agg,
                "llm": llm_agg,
            }
        }
# ── end SessionRecorder ──────────────────────────────────────────────

video_client = VideoStreamClient(host=RECEIVER_IP, port=LABEL)

video_supervisor = ProcessSupervisor(
    name="videoStreamProcess",
    start_func=video_client.start_video_stream,
    restart_delay=2,
)

# State lock for sending unified payload to MQTT
state_lock = threading.Lock()

latest_state = {
    "timestamp": None,
    "browser": {
        "task": "unknown",
        "correct_click": 0,
        "wrong_click": 0,
    },
    "mouse": {
        "idle_time": 0.0,
        "mouse_status": "unknown",
        "interval_clicks_per_second": 0.0,
        "overall_clicks_per_second": 0.0,
        "top_quadrant": "unknown",
    },
    "face": {
        "face_detected": False,
        "frustration_score": 0.0,
        "attention_score": 0.0,
        "emotion": "N/A",
        "direction": "N/A",
        "gaze_quadrant": "NO_FACE",
        "blink_rate": 0.0,
        "avg_ear": 0.0,
    },
    "llm": {
        "llm_activated": False,
        "last_role": None,
        "last_message": "",
        "llm_timeout": False
    },
    "alerts": {
        "frustration": False
    }
}


# Helpers for MQTT payload
def update_browser_state(data):
    with state_lock:
        latest_state["browser"].update(data)
        latest_state["timestamp"] = time.time()


def update_mouse_state(data):
    with state_lock:
        latest_state["mouse"].update(data)
        latest_state["timestamp"] = time.time()


def update_face_state(data):
    with state_lock:
        latest_state["face"].update(data)
        latest_state["timestamp"] = time.time()

def update_llm_state(data):
    with state_lock:
        latest_state["llm"].update(data)
        latest_state["timestamp"] = time.time()

def get_state_snapshot():
    with state_lock:
        return copy.deepcopy(latest_state)


# Start-up sequence config
face_sensor = None
face_ready_event = threading.Event()
shutdown_event = threading.Event()

# ================================
# Setup UAT Monitor
uat_monitor = UATMonitor()

uat_monitor.add_task(
    UATTask(task_name="Start Session", target_ids=[], success_id="btn-start-task")
)

uat_monitor.add_task(
    UATTask(
        task_name="Click the Color", target_ids=["color-blue"], success_id="color-blue"
    )
)

uat_monitor.add_task(
    UATTask(
        task_name="Number Selections",
        target_ids=["label-1", "label-3", "label-7"],
        success_id="btn-submit-selection",
        selection_ids=["label-1", "label-3", "label-7"],
    )
)


# ================================
# UAT → Flask bridge
def uat_bridge_loop():
    last_snapshot = None

    while True:
        try:
            metrics = uat_monitor.generate_metrics()
            current = metrics["currentTask"]

            snapshot = (
                current.get("taskName"),
                current.get("correct_click"),
                current.get("wrong_click"),
            )

            # MQTT
            browser_payload = {
                "task": current.get("taskName", "unknown"),
                "correct_click": current.get("correct_click", 0),
                "wrong_click": current.get("wrong_click", 0),
            }

            update_browser_state(browser_payload)

            # HTTP for LLM
            if snapshot != last_snapshot:
                requests.post(
                    "http://127.0.0.1:5000/api/browser_event",
                    json={
                        "type": "task_state",
                        "task": current.get("taskName", "unknown"),
                        "correct_click": current.get("correct_click", 0),
                        "wrong_click": current.get("wrong_click", 0),
                    },
                    timeout=0.5,
                )

                if current.get("wrong_click", 0) > 0:
                    requests.post(
                        "http://127.0.0.1:5000/api/browser_event",
                        json={
                            "type": "form_error",
                            "target": current.get("taskName", "unknown"),
                        },
                        timeout=0.5,
                    )

                last_snapshot = snapshot

        except Exception as e:
            print("[UAT Bridge Error]", e)

        time.sleep(1)


# ================================
# Mouse → Flask bridge
mouse_tracker = MouseTracker(idle_threshold=5, interval=1)


def mouse_bridge_loop():
    last_snapshot = None

    while True:
        try:
            metrics = mouse_tracker.generate_metrics()

            snapshot = (
                metrics.get("idle_time"),
                metrics.get("mouse_status"),
                metrics.get("interval_clicks_per_second"),
                metrics.get("overall_clicks_per_second"),
                metrics.get("top_quadrant"),
            )

            # MQTT
            mouse_payload = {
                "idle_time": metrics.get("idle_time", 0.0),
                "mouse_status": metrics.get("mouse_status", "unknown"),
                "interval_clicks_per_second": metrics.get(
                    "interval_clicks_per_second", 0.0
                ),
                "overall_clicks_per_second": metrics.get(
                    "overall_clicks_per_second", 0.0
                ),
                "top_quadrant": metrics.get("top_quadrant", "unknown"),
            }

            update_mouse_state(mouse_payload)

            # HTTP for LLM
            if snapshot != last_snapshot:
                requests.post(
                    "http://127.0.0.1:5000/api/mouse_event",
                    json=metrics,
                    timeout=0.5,
                )
                last_snapshot = snapshot

        except Exception as e:
            print("[Mouse Bridge Error]", e)

        time.sleep(1)


def llm_bridge_loop():
    while True:
        try:
            resp = requests.get("http://127.0.0.1:5000/api/llm_state", timeout=1.0)
            data = resp.json()

            llm_payload = {
                "llm_activated": data.get("llm_activated", False),
                "last_role": data.get("last_role"),
                "last_message": data.get("last_message", ""),
                "llm_timeout": data.get("llm_timeout", False),
            }

            update_llm_state(llm_payload)

        except Exception as e:
            print("[LLM Bridge Error]", e)

        time.sleep(0.5)


# ================================
# Face


# Resolution helper
def get_screen_resolution():
    try:
        out = (
            subprocess.check_output("xrandr | grep '*' | awk '{print $1}'", shell=True)
            .decode()
            .strip()
            .split("\n")[0]
        )
        w, h = map(int, out.split("x"))
        return w, h
    except Exception:
        return 1920, 1080


# Face start-up calibration
def calibrate_face_sensor():
    global face_sensor
    try:
        screen_w, screen_h = get_screen_resolution()
        face_sensor = FaceSensor(screen_w, screen_h, debug=True)

        print("[Face Bridge] Starting calibration...")
        face_sensor.calibrate()
        print("[Face Bridge] Calibration done.")
        face_ready_event.set()
        print("[Face Bridge] face_ready_event set")

    except Exception as e:
        print("[Face Calibration Error]", e)
        shutdown_event.set()


# Face → Flask bridge
def face_bridge_loop():
    global face_sensor
    last_snapshot = None
    last_post_time = 0.0

    print("[Face Bridge] Waiting for ready event...")
    face_ready_event.wait()
    print("[Face Bridge] Loop started")

    if face_sensor is None:
        print("[Face Bridge Error] Face sensor not initialised.")
        return

    try:
        while not shutdown_event.is_set():
            face_result = face_sensor.update()
            now = time.time()

            # DEBUG
            # print("[1] raw face_result:", face_result)

            # if face_result and face_result.get("face_detected"):
            #     print("[2] detected real face data")
            # else:
            #     print("[2] using default payload branch")

            if now - last_post_time < 1.0:
                time.sleep(0.01)
                continue

            if face_result and face_result.get("face_detected"):
                payload = {
                    "type": "face_state",
                    "face_detected": True,
                    "frustration_score": face_result.get("frustration_score", 0.0),
                    "attention_score": face_result.get("attention_score", 0.0),
                    "emotion": face_result.get("emotion", "N/A"),
                    "direction": face_result.get("direction", "N/A"),
                    "gaze_quadrant": face_result.get("gaze_quadrant", "UNCALIBRATED"),
                    "blink_rate": face_result.get("blink_rate", 0.0),
                    "avg_ear": face_result.get("avg_ear", 0.0),
                }
            else:
                payload = {
                    "type": "face_state",
                    "face_detected": False,
                    "frustration_score": 0.0,
                    "attention_score": 0.0,
                    "emotion": "N/A",
                    "direction": "N/A",
                    "gaze_quadrant": "NO_FACE",
                    "blink_rate": 0.0,
                    "avg_ear": 0.0,
                }

            snapshot = (
                payload["face_detected"],
                payload["frustration_score"],
                payload["attention_score"],
                payload["emotion"],
                payload["direction"],
                payload["gaze_quadrant"],
                payload["blink_rate"],
            )

            # MQTT
            face_payload = {
                "face_detected": payload["face_detected"],
                "frustration_score": payload["frustration_score"],
                "attention_score": payload["attention_score"],
                "emotion": payload["emotion"],
                "direction": payload["direction"],
                "gaze_quadrant": payload["gaze_quadrant"],
                "blink_rate": payload["blink_rate"],
                "avg_ear": payload["avg_ear"],
            }

            update_face_state(face_payload)

            # DEBUG
            # print("[3] shared face state now:", get_state_snapshot()["face"])

            # print("[Face Bridge] updated shared face state:", face_payload)

            # HTTP for LLM
            if snapshot != last_snapshot:
                try:
                    requests.post(
                        "http://127.0.0.1:5000/api/face_event",
                        json=payload,
                        timeout=(0.2, 1.5),  # connect timeout, read timeout
                    )
                    last_snapshot = snapshot
                except requests.RequestException as e:
                    print("[Face Bridge HTTP Error]", e)

            last_post_time = now

    except Exception as e:
        print("[Face Bridge Error]", e)
    finally:
        if face_sensor is not None:
            face_sensor.stop()


# ================================
# MQTT Functionalities
# =================================

# Shared MQTT client reference (set by mqtt_publish_loop, used by summary loop)
_mqtt_client_ref: "MQTTClient | None" = None
_mqtt_client_ref_lock = threading.Lock()

session_recorder = SessionRecorder(label=LABEL)
SGT = timezone(timedelta(hours=8))
session_id = datetime.now(SGT).strftime("%Y-%m-%d_%H-%M-%S")

# MQTT Loop
def mqtt_publish_loop():
    global _mqtt_client_ref

    broker_ip = RECEIVER_IP
    label = LABEL

    mqtt_client = MQTTClient(broker_ip=broker_ip, label=label)

    with _mqtt_client_ref_lock:
        _mqtt_client_ref = mqtt_client

    last_published_llm_message = None
    last_published_llm_role = None

    while True:
        try:
            snapshot = get_state_snapshot()
            current_llm_message = snapshot.get("llm", {}).get("last_message", "")

            if current_llm_message and current_llm_message == last_published_llm_message:
                snapshot["llm"]["last_message"] = ""
                snapshot["llm"]["last_role"] = None
            elif current_llm_message:
                last_published_llm_message = current_llm_message

            snapshot["alerts"]["frustration"] = session_recorder._get_frustration()

            # Record every snapshot for end-of-session summary
            session_recorder.record(snapshot)

            # print("[4] mqtt snapshot face:", snapshot["face"])
            payload = mqtt_client.build_payload(label, snapshot, session_id)
            mqtt_client.publish_tick(payload)
        except Exception as e:
            print("[MQTT Publish Error]", e)

        time.sleep(0.5)


def session_summary_loop():
    """
    Polls app.py for the session-complete signal.
    On completion:
      1. Builds and publishes the aggregated summary  (uat/summary, QoS 1)
      2. Publishes the full snapshot timeline as replay fragments (uat/replay, QoS 1)
    """
    print("[Summary Loop] Watching for session complete signal...")
 
    while True:
        try:
            resp = requests.get(
                "http://127.0.0.1:5000/api/session_complete_status", timeout=1.0
            )
            data = resp.json()
 
            if data.get("complete"):
                print("[Summary Loop] Session complete detected — building summary...")
 
                summary = session_recorder.build_summary(session_id)
                summary_payload = json.dumps(summary)
 
                with _mqtt_client_ref_lock:
                    client = _mqtt_client_ref
 
                if client is None:
                    print("[Summary Loop] MQTT client not ready — summary not sent.")
                    return
 
                # ── Step 1: publish aggregated summary ──────────────────
                client.publish_summary(summary_payload)
            
                # ── Save locally ──────────────────────────────────
                with session_recorder._lock:
                    snapshots = list(session_recorder._snapshots)
                try:
                    log_dir = Path("session_logs")
                    log_dir.mkdir(parents=True, exist_ok=True)
                    log_path = log_dir / f"session_{LABEL}_{session_id}.jsonl"
                    with open(log_path, "w") as f:
                        for snap in snapshots:
                            f.write(json.dumps(snap) + "\n")
                        f.write(summary_payload + "\n")
                    print(f"[Summary Loop] Local log saved: {log_path} ({len(snapshots)} ticks)")
                except Exception as e:
                    print(f"[Summary Loop] Local log failed: {e}")
                # ──────────────────────────────────────────────────────────
                
                # ── Step 2: publish full snapshot replay ─────────────────
                # session_recorder._snapshots is the complete in-memory list.
                if snapshots:
                    fragments_sent = client.publish_replay(
                        session_id=session_id,
                        snapshots=snapshots,
                        label=client.label,
                    )
                    print(f"[Summary Loop] Replay complete — {fragments_sent} fragments sent.")
                else:
                    print("[Summary Loop] No snapshots to replay.")
 
                return  # One-shot: exit after publishing
 
        except Exception as e:
            print("[Summary Loop Error]", e)
 
        import time
        time.sleep(3)


# ================================
# Main

if __name__ == "__main__":
    print("[Tracker Bridge] Starting...")

    # Start mouse tracker thread first (leave it ready in background)
    threading.Thread(target=mouse_tracker.start, daemon=True).start()

    # Start blocking face calibration first
    calibrate_face_sensor()

    # If calibration failed, stop startup
    if shutdown_event.is_set():
        raise SystemExit(1)

    # Only after calibration, open the browser
    web_tracker = WebTracker(uat_monitor, interval=1, url="http://127.0.0.1:5000")
    threading.Thread(target=web_tracker.start, daemon=True).start()

    # Start bridge loops
    threading.Thread(target=uat_bridge_loop, daemon=True).start()
    threading.Thread(target=mouse_bridge_loop, daemon=True).start()

    # threading.Thread(target=face_bridge_loop, daemon=True).start()
    print("[Main] starting face_bridge_loop thread")
    t = threading.Thread(target=face_bridge_loop, daemon=True, name="face_bridge")
    print("[Main] created thread", t.name)
    t.start()
    print("[Main] started thread", t.name)

    threading.Thread(target=llm_bridge_loop, daemon=True).start()
    threading.Thread(target=mqtt_publish_loop, daemon=True).start()
    threading.Thread(target=session_summary_loop, daemon=True).start()

    # Keep main thread alive
    try:
        while True:
            video_supervisor.ensure_running()
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nShutting down...")
        video_supervisor.stop()