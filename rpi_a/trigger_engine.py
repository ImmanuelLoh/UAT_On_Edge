import time
from config import TRIGGER_THRESHOLD, NUDGE_THRESHOLD, COOLDOWN_SECONDS


class TriggerEngine:
    def __init__(self):
        self.last_trigger_time = 0
        self.last_reason = None

    def in_cooldown(self) -> bool:
        return (time.time() - self.last_trigger_time) < COOLDOWN_SECONDS

    def evaluate(self, context_summary: dict) -> dict:
        if self.in_cooldown():
            return {
                "triggered": False,
                "nudged": False,
                "score": 0.0,
                "reason": "cooldown",
                "cooldown": True,
            }

        score = 0.0
        reasons = []

        rage_clicks = context_summary.get("rage_clicks", 0)
        repeated_target = context_summary.get("repeated_target", False)
        frustration = context_summary.get("frustration_score", 0.0)
        form_errors = context_summary.get("form_errors", 0)
        stall_seconds = context_summary.get("stall_seconds", 0)
        task_wrong_clicks = context_summary.get("task_wrong_clicks", 0)
        idle_time = context_summary.get("idle_time", 0)
        mouse_status = context_summary.get("mouse_status", "unknown")

        if rage_clicks >= 3:
            score += 0.4
            reasons.append("rage_clicks")

        if repeated_target:
            score += 0.2
            reasons.append("repeated_target")

        if frustration >= 0.7:
            score += 0.4
            reasons.append("high_frustration")

        if form_errors >= 2:
            score += 0.2
            reasons.append("repeated_form_errors")  

        if stall_seconds >= 8:
            score += 0.2
            reasons.append("task_stall")
        
        if task_wrong_clicks >= 2:
            score += 0.3
            reasons.append("task_wrong_clicks")

        if idle_time >= 8:
            score += 0.2
            reasons.append("mouse_idle")

        if mouse_status == "IDLE" and context_summary.get("task") not in ["unknown", "Start Session"]:
            score += 0.1
            reasons.append("inactive_during_task")

        reason = " + ".join(reasons) if reasons else None

        if score >= TRIGGER_THRESHOLD:
            self.last_trigger_time = time.time()
            self.last_reason = reason
            return {
                "triggered": True,
                "nudged": False,
                "score": round(score, 2),
                "reason": reason,
                "cooldown": False,
            }

        if score >= NUDGE_THRESHOLD:
            return {
                "triggered": False,
                "nudged": True,
                "score": round(score, 2),
                "reason": reason,
                "cooldown": False,
            }

        return {
            "triggered": False,
            "nudged": False,
            "score": round(score, 2),
            "reason": reason,
            "cooldown": False,
        }
