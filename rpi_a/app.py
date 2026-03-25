from flask import Flask, render_template, request, jsonify, redirect, url_for
import time
import threading

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

# for state tracking
last_seen_task = None

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

# ============= LLM State for MQTT =============
llm_state_lock = threading.Lock()
llm_state = {
    "llm_activated": False,
    "last_role": None,
    "last_message": "",
}


def record_llm_event(role: str, message: str):
    with llm_state_lock:
        llm_state["llm_activated"] = True
        llm_state["last_role"] = role
        llm_state["last_message"] = message


# =======  END: LLM State for MQTT =============


# Ensure assistant doesn't run on page 1 & 4
def is_assistant_allowed(summary: dict) -> bool:
    return summary.get("task") in ["Click the Color", "Number Selections"]


def get_page_context(summary: dict) -> dict:
    task = summary.get("task", "unknown")

    if task == "Click the Color":
        return {
            "page_name": "Page 2 - Task 2",
            "goal": "Click the blue square.",
            "instruction_text": "The task is to click the BLUE square.",
            "visible_elements": [
                {"type": "color square", "label": "red square", "position": "top-left"},
                {
                    "type": "color square",
                    "label": "blue square",
                    "position": "top-right",
                },
                {
                    "type": "color square",
                    "label": "green square",
                    "position": "bottom-left",
                },
                {
                    "type": "color square",
                    "label": "yellow square",
                    "position": "bottom-right",
                },
            ],
            "allowed_elements": [
                "red square",
                "blue square",
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
            "instruction_text": "Select exactly 3 numbers: 1, 3, and 7. Then submit the selection.",
            "visible_elements": [
                {"type": "number tile", "label": "1"},
                {"type": "number tile", "label": "2"},
                {"type": "number tile", "label": "3"},
                {"type": "number tile", "label": "4"},
                {"type": "number tile", "label": "5"},
                {"type": "number tile", "label": "6"},
                {"type": "number tile", "label": "7"},
                {"type": "number tile", "label": "8"},
                {"type": "number tile", "label": "9"},
                {"type": "number tile", "label": "10"},
                {"type": "number tile", "label": "11"},
                {"type": "number tile", "label": "12"},
                {"type": "button", "label": "Submit Selection button"},
            ],
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
        "instruction_text": "",
        "visible_elements": [],
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


# Helper to reset state in LLM assistant
def reset_assistant_for_new_task():
    latest_ui_state["assistant_open"] = False
    latest_ui_state["assistant_message"] = ""
    latest_ui_state["proactive_message"] = ""
    latest_ui_state["chat_message"] = ""
    latest_ui_state["nudge"] = False
    latest_ui_state["chat_mode"] = False
    latest_ui_state["score"] = 0.0
    latest_ui_state["reason"] = None

    # with llm_state_lock:
    #     llm_state["llm_activated"] = False
    #     llm_state["last_role"] = None
    #     llm_state["last_message"] = ""


def reevaluate_assistant():
    global assistant_dismissed_until

    summary = context_buffer.summarize()

    if not is_assistant_allowed(summary):
        latest_ui_state["assistant_open"] = False
        latest_ui_state["assistant_message"] = ""
        latest_ui_state["proactive_message"] = ""
        latest_ui_state["chat_message"] = ""
        latest_ui_state["nudge"] = False
        latest_ui_state["chat_mode"] = False
        latest_ui_state["score"] = 0.0
        latest_ui_state["reason"] = None

        return {
            "triggered": False,
            "nudged": False,
            "score": 0.0,
            "reason": "assistant_disabled_for_page",
            "cooldown": False,
        }

    result = trigger_engine.evaluate(summary)

    # Turn nudge on (do not turn it off automatically)
    if result["nudged"]:
        latest_ui_state["nudge"] = True

    latest_ui_state["score"] = result["score"]
    latest_ui_state["reason"] = result["reason"]

    if (
        result["triggered"]
        # and not latest_ui_state["chat_mode"] # Only allow proactive message when chat is CLOSED not minimised
        and time.time() > assistant_dismissed_until
    ):
        # Inject context into LLM Payload
        summary = context_buffer.summarize()
        summary["page_context"] = get_page_context(summary)
        summary["trigger_reason"] = latest_ui_state.get("reason")
        # summary["trigger_score"] = latest_ui_state.get("score") # Don't push score to LLM (use it as a trigger ONLY)

        llm_reply = request_assistance(summary, mode="proactive")
        reply_text = llm_reply.get(
            "assistant_message", ""
        ).strip() or build_fallback_hint(summary)

        record_llm_event("assistant", reply_text)  # For MQTT dashboard

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
    global last_seen_task

    data = request.get_json(force=True) or {}
    data["ts"] = time.time()

    event_type = data.get("type")

    # Convert frontend events into backend events
    if event_type == "task_submit_result":
        if data.get("result") == "incorrect":
            context_buffer.add_event(
                {"type": "form_error", "target": data.get("task"), "ts": data["ts"]}
            )
        # Don't store the original event
    else:
        context_buffer.add_event(data)

    # Handle task changes using explicit tracking
    if event_type == "task_state":
        new_task = data.get("task", "unknown")

        if new_task and new_task != last_seen_task:
            # Avoid resetting on the very first task_state i.e. None to "Start Session"
            # Task transitions should work for other pages since the starting state is not None
            if last_seen_task is not None:
                reset_assistant_for_new_task()

            last_seen_task = new_task

    # Manual help
    if event_type == "manual_help_open":

        # Inject context into LLM Payload
        summary = context_buffer.summarize()

        if not is_assistant_allowed(summary):
            return jsonify({"ok": True, "assistant_message": ""})

        summary["page_context"] = get_page_context(summary)
        summary["trigger_reason"] = latest_ui_state.get("reason")

        llm_reply = request_assistance(summary, mode="chat")
        reply_text = llm_reply.get(
            "assistant_message", ""
        ).strip() or build_fallback_hint(summary)

        record_llm_event("assistant", reply_text)  # For MQTT dashboard

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
    record_llm_event("user", user_msg)  # For MQTT dashboard
    summary = context_buffer.summarize()
    summary["user_message"] = user_msg

    if not is_assistant_allowed(summary):
        return jsonify({"assistant_message": ""})

    # Inject context into LLM payload
    summary["page_context"] = get_page_context(summary)
    summary["trigger_reason"] = latest_ui_state.get("reason")
    # summary["trigger_score"] = latest_ui_state.get("score")

    latest_ui_state["chat_mode"] = True

    llm_reply = request_assistance(summary, mode="chat")
    reply_text = llm_reply.get("assistant_message", "No response.")
    record_llm_event("assistant", reply_text)  # For MQTT dashboard

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


# Endpoint for MQTT dashboard to poll latest LLM state
@app.route("/api/llm_state")
def get_llm_state():
    with llm_state_lock:
        return jsonify(dict(llm_state))


if __name__ == "__main__":
    # thread = threading.Thread(target=sensor_loop, daemon=True)
    # thread.start()
    app.run(host="0.0.0.0", port=5000, debug=True)
