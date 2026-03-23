from flask import Flask, render_template, request, jsonify, redirect, url_for
import time

from context_buffer import ContextBuffer
from trigger_engine import TriggerEngine
from llm_client import request_assistance

# from sensors.simulated_mouse import get_mouse_events
# from sensors.simulated_face import get_face_state
# from sensors.simulated_task import get_task_state

app = Flask(__name__)

context_buffer = ContextBuffer()
trigger_engine = TriggerEngine()

# delay assistant from reopening when chat is manually closed
assistant_dismissed_until = 0

latest_ui_state = {
    "assistant_open": False,
    "assistant_message": "",
    "proactive_message": "",
    "chat_message": "",
    "nudge": False,
    "score": 0.0,
    "reason": None,
    "chat_mode": False,
}


def get_page_context(summary: dict) -> dict:
    task = summary.get("task", "unknown")

    if task == "Start Session":
        return {
            "page_name": "Page 1 - Task 1",
            "goal": "Click the Start button to begin the session.",
            "allowed_elements": ["Start button"],
            "forbidden_inferences": [
                "forms",
                "checkmarks",
                "color choices",
                "number tiles",
            ],
            "hint_policy": "Keep the hint short and mention only the Start button.",
        }

    if task == "Click the Color":
        return {
            "page_name": "Page 2 - Task 2",
            "goal": "Click the blue square.",
            "allowed_elements": [
                "blue square",
                "red square",
                "green square",
                "yellow square",
            ],
            "forbidden_inferences": [
                "checkmarks",
                "buttons",
                "different blue shades",
                "hidden controls",
            ],
            "hint_policy": "If there are repeated wrong clicks, tell the user to click the blue square.",
        }

    if task == "Number Selections":
        return {
            "page_name": "Page 3 - Task 3",
            "goal": "Select exactly 3 numbers: 1, 3, and 7. Then submit the selection.",
            "allowed_elements": ["number tiles", "Submit Selection button"],
            "forbidden_inferences": [
                "forms",
                "text fields",
                "previous step",
                "re-entering information",
            ],
            "hint_policy": "If the selection seems wrong, remind the user to choose exactly 1, 3, and 7.",
        }

    return {
        "page_name": task,
        "goal": "Help the user complete the current task.",
        "allowed_elements": [],
        "forbidden_inferences": [],
        "hint_policy": "Keep help brief and actionable.",
    }


def build_fallback_hint(summary: dict) -> str:
    task = summary.get("task", "unknown")
    wrong = summary.get("task_wrong_clicks", 0)

    if task == "Click the Color":
        if wrong >= 2:
            return "It looks like there were a few incorrect selections. Try clicking the blue square."
        return "Try clicking the blue square."

    if task == "Number Selections":
        return "The current selection seems incorrect. Select exactly 3 numbers: 1, 3, and 7, then submit."

    if task == "Start Session":
        return "Try clicking the Start button to begin the session."

    return "It looks like you may be stuck. Try the current task again carefully."


def reevaluate_assistant():
    global assistant_dismissed_until

    summary = context_buffer.summarize()
    result = trigger_engine.evaluate(summary)

    # Turn nudge on (do not turn it off automatically)
    if result["nudged"]:
        latest_ui_state["nudge"] = True

    latest_ui_state["score"] = result["score"]
    latest_ui_state["reason"] = result["reason"]

    if (
        result["triggered"]
        and not latest_ui_state["chat_mode"]
        and time.time() > assistant_dismissed_until
    ):
        # Inject context into LLM Payload
        summary = context_buffer.summarize()
        summary["page_context"] = get_page_context(summary)
        summary["trigger_reason"] = latest_ui_state.get("reason")
        summary["trigger_score"] = latest_ui_state.get("score")

        llm_reply = request_assistance(summary, mode="proactive")
        reply_text = llm_reply.get(
            "assistant_message", ""
        ).strip() or build_fallback_hint(summary)
        latest_ui_state["assistant_open"] = True
        latest_ui_state["proactive_message"] = reply_text
        latest_ui_state["assistant_message"] = reply_text

    return result


@app.route("/")
def page1():
    return render_template("page1.html")


@app.route("/task-color")
def page2():
    return render_template("page2.html")


@app.route("/task-selection", methods=["GET", "POST"])
def page3():
    if request.method == "POST":
        # Logic to check if exactly 3 are selected
        selected = request.form.getlist("options")
        if len(selected) == 3:
            return redirect(url_for("page4"))
    return render_template("page3.html")


@app.route("/complete")
def page4():
    return render_template("page4.html")


@app.route("/api/browser_event", methods=["POST"])
def browser_event():
    data = request.get_json(force=True) or {}
    data["ts"] = time.time()

    event_type = data.get("type")
    context_buffer.add_event(data)

    if event_type == "manual_help_open":

        # Inject context into LLM payload
        summary = context_buffer.summarize()
        summary["page_context"] = get_page_context(summary)
        summary["trigger_reason"] = latest_ui_state.get("reason")
        summary["trigger_score"] = latest_ui_state.get("score")

        llm_reply = request_assistance(summary, mode="proactive")
        reply_text = llm_reply.get(
            "assistant_message", ""
        ).strip() or build_fallback_hint(summary)

        latest_ui_state["assistant_open"] = True
        latest_ui_state["nudge"] = False
        latest_ui_state["proactive_message"] = reply_text
        latest_ui_state["assistant_message"] = reply_text

        return jsonify({"ok": True, "assistant_message": reply_text})

    result = reevaluate_assistant()
    return jsonify({"ok": True, "trigger_result": result})


@app.route("/api/mouse_event", methods=["POST"])
def mouse_event():
    data = request.get_json(force=True) or {}
    data["type"] = "mouse_state"
    data["ts"] = time.time()
    context_buffer.add_event(data)

    result = reevaluate_assistant()
    return jsonify({"ok": True, "trigger_result": result})


@app.route("/api/face_event", methods=["POST"])
def face_event():
    data = request.get_json(force=True) or {}

    event = {
        "type": "face_state",
        "face_detected": bool(data.get("face_detected", False)),
        "frustration_score": float(data.get("frustration_score", 0.0)),
        "attention_score": float(data.get("attention_score", 0.0)),
        "emotion": data.get("emotion", "N/A"),
        "direction": data.get("direction", "N/A"),
        "gaze_quadrant": data.get("gaze_quadrant", "UNCALIBRATED"),
        "blink_rate": float(data.get("blink_rate", 0.0)),
        "avg_ear": float(data.get("avg_ear", 0.0)),
        "ts": time.time(),
    }

    context_buffer.add_event(event)
    result = reevaluate_assistant()
    return jsonify({"ok": True, "trigger_result": result})


@app.route("/api/ui_state")
def ui_state():
    return jsonify(latest_ui_state)


@app.route("/api/chat_reply", methods=["POST"])
def chat_reply():
    user_msg = request.get_json().get("message", "")
    summary = context_buffer.summarize()
    summary["user_message"] = user_msg

    # Inject context into LLM payload
    summary["page_context"] = get_page_context(summary)
    summary["trigger_reason"] = latest_ui_state.get("reason")
    summary["trigger_score"] = latest_ui_state.get("score")

    latest_ui_state["chat_mode"] = True

    llm_reply = request_assistance(summary, mode="chat")
    reply_text = llm_reply.get("assistant_message", "No response.")

    latest_ui_state["assistant_open"] = True
    latest_ui_state["chat_message"] = reply_text
    latest_ui_state["assistant_message"] = reply_text

    return jsonify(llm_reply)


@app.route("/api/close_chat", methods=["POST"])
def close_chat():
    global assistant_dismissed_until

    assistant_dismissed_until = time.time() + 20  # 20 second cooldown
    latest_ui_state["assistant_open"] = False
    latest_ui_state["chat_mode"] = False
    latest_ui_state["assistant_message"] = ""

    return jsonify({"ok": True})


if __name__ == "__main__":
    # thread = threading.Thread(target=sensor_loop, daemon=True)
    # thread.start()
    app.run(host="0.0.0.0", port=5000, debug=True)
