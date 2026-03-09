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

latest_ui_state = {
    "assistant_open": False,
    "assistant_message": "",
    "nudge": False,
    "score": 0.0,
    "reason": None,
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

        if result["triggered"]:
            llm_reply = request_assistance(summary)
            latest_ui_state["assistant_open"] = True
            latest_ui_state["assistant_message"] = llm_reply.get(
                "assistant_message",
                "I noticed you may be having trouble. Would you like help?",
            )

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

    if result["triggered"]:
        llm_reply = request_assistance(summary)
        latest_ui_state["assistant_open"] = True
        latest_ui_state["assistant_message"] = llm_reply.get(
            "assistant_message",
            "I noticed you may be having trouble. Would you like help?",
        )

    latest_ui_state["nudge"] = result["nudged"]
    latest_ui_state["score"] = result["score"]
    latest_ui_state["reason"] = result["reason"]

    return jsonify({"ok": True, "trigger_result": result})


@app.route("/api/ui_state")
def ui_state():
    return jsonify(latest_ui_state)


@app.route("/api/chat_reply", methods=["POST"])
def chat_reply():
    user_msg = request.get_json().get("message", "")
    summary = context_buffer.summarize()
    summary["user_message"] = user_msg

    llm_reply = request_assistance(summary)
    latest_ui_state["assistant_open"] = True
    latest_ui_state["assistant_message"] = llm_reply.get("assistant_message", "No response.")
    return jsonify(llm_reply)


@app.route("/api/close_chat", methods=["POST"])
def close_chat():
    latest_ui_state["assistant_open"] = False
    return jsonify({"ok": True})


if __name__ == "__main__":
    thread = threading.Thread(target=sensor_loop, daemon=True)
    thread.start()
    app.run(host="0.0.0.0", port=5000, debug=True)