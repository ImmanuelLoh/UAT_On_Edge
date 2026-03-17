from flask import Flask, render_template, request, jsonify, redirect, url_for
import threading
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


# def sensor_loop():
#     while True:
#         # simulated task + face state
#         context_buffer.add_event(get_face_state())
#         context_buffer.add_event(get_task_state())

#         # simulated mouse events
#         for event in get_mouse_events():
#             context_buffer.add_event(event)

#         summary = context_buffer.summarize()
#         result = trigger_engine.evaluate(summary)

#         latest_ui_state["nudge"] = result["nudged"]
#         latest_ui_state["score"] = result["score"]
#         latest_ui_state["reason"] = result["reason"]

#         if (result["triggered"] and not latest_ui_state["chat_mode"] and time.time() > assistant_dismissed_until):
#             llm_reply = request_assistance(summary, mode="proactive")
#             reply_text = llm_reply.get(
#                 "assistant_message",
#                 "It looks like you may be stuck. Try checking the highlighted field."
#             )
#             latest_ui_state["assistant_open"] = True
#             latest_ui_state["proactive_message"] = reply_text
#             latest_ui_state["assistant_message"] = reply_text

#         time.sleep(2)



# @app.route("/")
# def index():
#     return render_template("index.html")

def reevaluate_assistant():
    global assistant_dismissed_until

    summary = context_buffer.summarize()
    result = trigger_engine.evaluate(summary)

    latest_ui_state["nudge"] = result["nudged"]
    latest_ui_state["score"] = result["score"]
    latest_ui_state["reason"] = result["reason"]

    if (
        result["triggered"]
        and not latest_ui_state["chat_mode"]
        and time.time() > assistant_dismissed_until
    ):
        llm_reply = request_assistance(summary, mode="proactive")
        reply_text = llm_reply.get(
            "assistant_message",
            "It looks like you may be stuck. Try checking the highlighted field."
        )
        latest_ui_state["assistant_open"] = True
        latest_ui_state["proactive_message"] = reply_text
        latest_ui_state["assistant_message"] = reply_text

    return result

@app.route('/')
def page1():
    return render_template('page1.html')

@app.route('/task-color')
def page2():
    return render_template('page2.html')

@app.route('/task-selection', methods=['GET', 'POST'])
def page3():
    if request.method == 'POST':
        # Logic to check if exactly 3 are selected
        selected = request.form.getlist('options')
        if len(selected) == 3:
            return redirect(url_for('page4'))
    return render_template('page3.html')

@app.route('/complete')
def page4():
    return render_template('page4.html')


@app.route("/api/browser_event", methods=["POST"])
def browser_event():
    data = request.get_json(force=True) or {}
    data["ts"] = time.time()
    context_buffer.add_event(data)

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

# @app.route("/api/face_event", methods=["POST"])
# def face_event():
#     data = request.get_json(force=True) or {}

#     event = {
#         "type": "face_state",
#         "frustration_score": float(data.get("frustration_score", 0.0)),
#         "gaze_state": data.get("gaze_state", "unknown"),
#         "attention_score": data.get("attention_score"),
#         "emotion": data.get("emotion"),
#         "blink_rate": data.get("blink_rate"),
#         "ts": time.time(),
#     }

#     context_buffer.add_event(event)
#     result = reevaluate_assistant()
#     return jsonify({"ok": True, "trigger_result": result})

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
    # thread = threading.Thread(target=sensor_loop, daemon=True)
    # thread.start()
    app.run(host="0.0.0.0", port=5000, debug=True)