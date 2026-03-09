from flask import Flask, render_template, request, jsonify
import threading
import time

from context_buffer import ContextBuffer
from trigger_engine import TriggerEngine
from llm_client import request_assistance

from sensors.simulated_mouse import get_mouse_events
from sensors.simulated_face import get_face_state
from sensors.simulated_task import get_task_state

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


def sensor_loop():
    while True:
        # simulated task + face state
        context_buffer.add_event(get_face_state())
        context_buffer.add_event(get_task_state())

        # simulated mouse events
        for event in get_mouse_events():
            context_buffer.add_event(event)

        summary = context_buffer.summarize()
        result = trigger_engine.evaluate(summary)

        latest_ui_state["nudge"] = result["nudged"]
        latest_ui_state["score"] = result["score"]
        latest_ui_state["reason"] = result["reason"]

        if (result["triggered"] and not latest_ui_state["chat_mode"] and time.time() > assistant_dismissed_until):
            llm_reply = request_assistance(summary, mode="proactive")
            reply_text = llm_reply.get(
                "assistant_message",
                "It looks like you may be stuck. Try checking the highlighted field."
            )
            latest_ui_state["assistant_open"] = True
            latest_ui_state["proactive_message"] = reply_text
            latest_ui_state["assistant_message"] = reply_text

        time.sleep(2)


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/browser_event", methods=["POST"])
def browser_event():
    data = request.get_json()
    data["ts"] = time.time()
    context_buffer.add_event(data)

    summary = context_buffer.summarize()
    result = trigger_engine.evaluate(summary)

    if result["triggered"] and not latest_ui_state["chat_mode"]:
        llm_reply = request_assistance(summary, mode="proactive")
        reply_text = llm_reply.get(
            "assistant_message",
            "It looks like you may be stuck. Try checking the highlighted field."
        )
        latest_ui_state["assistant_open"] = True
        latest_ui_state["proactive_message"] = reply_text
        latest_ui_state["assistant_message"] = reply_text

    return jsonify({"ok": True, "trigger_result": result})


@app.route("/api/ui_state")
def ui_state():
    return jsonify(latest_ui_state)


@app.route("/api/chat_reply", methods=["POST"])
def chat_reply():
    user_msg = request.get_json().get("message", "")
    summary = context_buffer.summarize()
    summary["user_message"] = user_msg

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

    return jsonify({"ok": True})


if __name__ == "__main__":
    thread = threading.Thread(target=sensor_loop, daemon=True)
    thread.start()
    app.run(host="0.0.0.0", port=5000, debug=True)