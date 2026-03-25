import threading
import time
import requests
import subprocess

# MQTT
import copy
import json
import sys

from transmission.MQTTClient import MQTTClient
from transmission.VideoStreamClient import VideoStreamClient
from transmission.ProcessSupervisor import ProcessSupervisor

from sensors.face_sensor import FaceSensor
from sensors.uat_monitor import UATMonitor, UATTask
from sensors.web_tracker import WebTracker
from sensors.mouse_tracker import MouseTracker

# MQTT transmission config
RECEIVER_IP = sys.argv[1]
LABEL = int(sys.argv[2])

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
            print("[1] raw face_result:", face_result)

            if face_result and face_result.get("face_detected"):
                print("[2] detected real face data")
            else:
                print("[2] using default payload branch")

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
            print("[3] shared face state now:", get_state_snapshot()["face"])

            print("[Face Bridge] updated shared face state:", face_payload)

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


# MQTT Loop
def mqtt_publish_loop():
    broker_ip = "192.168.0.144"
    label = 5002

    mqtt_client = MQTTClient(broker_ip=broker_ip, label=label)

    while True:
        try:
            snapshot = get_state_snapshot()
            print("[4] mqtt snapshot face:", snapshot["face"])
            payload = mqtt_client.build_payload(label, snapshot)
            mqtt_client.publish(payload)
        except Exception as e:
            print("[MQTT Publish Error]", e)

        time.sleep(0.5)


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

    threading.Thread(target=mqtt_publish_loop, daemon=True).start()

    # Keep main thread alive
    try:
        while True:
            video_supervisor.ensure_running()
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nShutting down...")
        video_supervisor.stop()
