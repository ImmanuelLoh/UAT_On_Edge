import threading
import time
import requests

from sensors.uat_monitor import UATMonitor, UATTask
from sensors.web_tracker import WebTracker
from sensors.mouse_tracker import MouseTracker


# ================================
# Setup UAT Monitor
uat_monitor = UATMonitor()

uat_monitor.add_task(UATTask(
    task_name="Start Session",
    target_ids=[],
    success_id="btn-start-task"
))

uat_monitor.add_task(UATTask(
    task_name="Click the Color",
    target_ids=[],
    success_id="color-blue"
))

uat_monitor.add_task(UATTask(
    task_name="Number Selections",
    target_ids=["label-1", "label-3", "label-7"],
    success_id="btn-submit-selection",
    selection_ids=["label-1", "label-3", "label-7"]
))


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
# Main

if __name__ == "__main__":
    print("[Tracker Bridge] Starting...")

    # Start mouse tracking thread
    threading.Thread(target=mouse_tracker.start, daemon=True).start()

    # Start web tracker (Selenium)
    web_tracker = WebTracker(uat_monitor, interval=1, url="http://127.0.0.1:5000")
    threading.Thread(target=web_tracker.start, daemon=True).start()

    # Start bridge loops
    threading.Thread(target=uat_bridge_loop, daemon=True).start()
    threading.Thread(target=mouse_bridge_loop, daemon=True).start()

    # Keep main thread alive
    while True:
        time.sleep(1)