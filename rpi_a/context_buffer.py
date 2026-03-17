import time
from collections import deque
from config import CONTEXT_WINDOW_SECONDS


class ContextBuffer:
    def __init__(self):
        self.events = deque()

    def add_event(self, event: dict):
        event["ts"] = event.get("ts", time.time())
        self.events.append(event)
        self._prune()

    def _prune(self):
        now = time.time()
        while self.events and (now - self.events[0]["ts"] > CONTEXT_WINDOW_SECONDS):
            self.events.popleft()

    def summarize(self) -> dict:
        self._prune()
        events = list(self.events)

        click_events = [e for e in events if e.get("type") == "click"]
        form_errors = [e for e in events if e.get("type") == "form_error"]
        mouse_targets = [e.get("target") for e in click_events if e.get("target")]

        rage_clicks = len(click_events)
        repeated_target = False
        if mouse_targets:
            repeated_target = mouse_targets.count(mouse_targets[-1]) >= 3

        mouse_status = "unknown"
        idle_time = 0
        click_rate = 0.0
        overall_click_rate = 0.0
        top_quadrant = None

        frustration_score = 0.0
        gaze_state = "unknown"

        current_task = "unknown"
        task_wrong_clicks = 0
        task_correct_clicks = 0

        for e in reversed(events):
            if e.get("type") == "mouse_state":
                mouse_status = e.get("mouse_status", "unknown")
                idle_time = e.get("idle_time", 0)
                click_rate = e.get("interval_clicks_per_second", 0.0)
                overall_click_rate = e.get("overall_clicks_per_second", 0.0)
                top_quadrant = e.get("top_quadrant")
                break

        for e in reversed(events):
            if e.get("type") == "face_state":
                frustration_score = e.get("frustration_score", 0.0)
                gaze_state = e.get("gaze_state", "unknown")
                break

        for e in reversed(events):
            if e.get("type") == "task_state":
                current_task = e.get("task", "unknown")
                task_wrong_clicks = e.get("wrong_click", 0)
                task_correct_clicks = e.get("correct_click", 0)
                break

        now = time.time()
        stall_seconds = 0
        if events:
            stall_seconds = int(now - events[-1]["ts"])

        recent_actions = [
            {
                "type": e.get("type"),
                "target": e.get("target"),
                "task": e.get("task"),
                "frustration_score": e.get("frustration_score"),
            }
            for e in events[-8:]
        ]

        return {
            "task": current_task,
            "task_wrong_clicks": task_wrong_clicks,
            "task_correct_clicks": task_correct_clicks,
            "frustration_score": frustration_score,
            "gaze_state": gaze_state,
            "rage_clicks": rage_clicks,
            "repeated_target": repeated_target,
            "form_errors": len(form_errors),
            "stall_seconds": stall_seconds,
            "mouse_status": mouse_status,
            "idle_time": idle_time,
            "click_rate": click_rate,
            "overall_click_rate": overall_click_rate,
            "top_quadrant": top_quadrant,
            "recent_actions": recent_actions,
        }
