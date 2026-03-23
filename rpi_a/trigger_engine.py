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

        frustration = context_summary.get("frustration_score", 0.0)
        attention_score = context_summary.get("attention_score", 0.0)
        face_detected = context_summary.get("face_detected", False)
        gaze_quadrant = context_summary.get("gaze_quadrant", "UNCALIBRATED")
        direction = context_summary.get("direction", "N/A")
        task = context_summary.get("task", "unknown")
        form_errors = context_summary.get("form_errors", 0)
        stall_seconds = context_summary.get("stall_seconds", 0)
        task_wrong_clicks = context_summary.get("task_wrong_clicks", 0)
        idle_time = context_summary.get("idle_time", 0)
        mouse_status = context_summary.get("mouse_status", "unknown")

        if frustration >= 70:
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
            
        if face_detected and attention_score <= 35:
            score += 0.3
            reasons.append("low_attention")

        if face_detected and direction != "FORWARD":
            score += 0.1
            reasons.append("off_center_head_pose")

        if face_detected and gaze_quadrant in ["TOP-LEFT", "TOP-RIGHT", "BOTTOM-LEFT", "BOTTOM-RIGHT"]:
            score += 0.1
            reasons.append("off_target_gaze")

        if not face_detected and task not in ["unknown", "Start Session"]:
            score += 0.1
            reasons.append("face_missing_during_task")            

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
