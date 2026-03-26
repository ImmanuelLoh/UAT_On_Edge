"""
payload_parsers.py
Pure data-parsing functions — no Qt, no MQTT, no Firebase.

Imported by both dashboard_ui.py (for display) and mqtt_dashboard.py (for
validation/logging).
"""

import logging

logger = logging.getLogger(__name__)


def parse_mqtt_payload(data: dict) -> dict | None:
    """
    Parse a raw MQTT tick payload (from uat/raw) into structured display fields.
    Returns None if parsing fails or required keys are missing.
    """
    try:
        browser = data.get("browser", {})
        mouse   = data.get("mouse", {})
        face    = data.get("face", {})
        llm     = data.get("llm", {})

        return {
            "task":              browser.get("task", "—"),
            "correct_click":     browser.get("correct_click", "—"),
            "wrong_click":       browser.get("wrong_click", "—"),
            "mouse_status":      mouse.get("mouse_status", "—"),
            "idle_time":         mouse.get("idle_time", "—"),
            "clicks_per_second": mouse.get("overall_clicks_per_second", "—"),
            "top_quadrant":      mouse.get("top_quadrant") or "—",
            "face_detected":     face.get("face_detected", "—"),
            "emotion":           face.get("emotion", "—"),
            "frustration_score": face.get("frustration_score", "—"),
            "attention_score":   face.get("attention_score", "—"),
            "direction":         face.get("direction", "—"),
            "gaze_quadrant":     face.get("gaze_quadrant", "—"),
            "blink_rate":        face.get("blink_rate", "—"),
            "llm_activated":     llm.get("llm_activated", False),
            "llm_last_role":     llm.get("last_role"),
            "llm_last_message":  llm.get("last_message", ""),
        }
    except Exception as e:
        logger.warning(f"[Parser] Failed to parse tick payload: {e}")
        return None


def parse_summary_payload(data: dict) -> dict | None:
    """
    Parse an end-of-session summary payload (from uat/summary) into
    display-ready fields. Returns None if parsing fails.
    """
    try:
        meta  = data.get("meta", {})
        agg   = data.get("aggregates", {})
        face  = agg.get("face", {})
        mouse = agg.get("mouse", {})
        brow  = agg.get("browser", {})
        llm   = agg.get("llm", {})

        duration_s = meta.get("duration_seconds", 0)
        mins, secs = divmod(int(duration_s), 60)

        emotion_counts: dict = face.get("emotion_counts", {})
        dominant_emotion = max(emotion_counts, key=emotion_counts.get) if emotion_counts else "N/A"

        gaze_counts: dict = face.get("gaze_quadrant_counts", {})
        dominant_gaze = max(gaze_counts, key=gaze_counts.get) if gaze_counts else "N/A"

        quadrant_counts: dict = mouse.get("top_quadrant_counts", {})
        dominant_quadrant = max(quadrant_counts, key=quadrant_counts.get) if quadrant_counts else "N/A"

        return {
            # Meta
            "label":           meta.get("label", "—"),
            "session_active":  meta.get("session_active", False),
            "duration":        f"{mins}m {secs:02d}s",
            "total_snapshots": meta.get("total_snapshots", "—"),
            # Face
            "avg_frustration":  face.get("avg_frustration_score", "—"),
            "peak_frustration": face.get("peak_frustration_score", "—"),
            "avg_attention":    face.get("avg_attention_score", "—"),
            "avg_blink_rate":   face.get("avg_blink_rate", "—"),
            "dominant_emotion": dominant_emotion,
            "dominant_gaze":    dominant_gaze,
            # Mouse
            "avg_idle_time":    mouse.get("avg_idle_time", "—"),
            "peak_idle_time":   mouse.get("peak_idle_time", "—"),
            "avg_cps":          mouse.get("avg_overall_clicks_per_second", "—"),
            "dominant_quadrant": dominant_quadrant,
            # Browser
            "total_wrong_clicks":   brow.get("total_wrong_clicks", "—"),
            "total_correct_clicks": brow.get("total_correct_clicks", "—"),
            # LLM
            "llm_activations":    llm.get("total_activations", 0),
            "assistant_messages": llm.get("assistant_messages", []),
        }
    except Exception as e:
        logger.warning(f"[SummaryParser] Failed to parse summary payload: {e}")
        return None