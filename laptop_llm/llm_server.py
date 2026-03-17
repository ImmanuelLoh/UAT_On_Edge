import requests
from flask import Flask, request, jsonify

app = Flask(__name__)


OLLAMA_URL = "http://localhost:11434/api/generate"
MODEL = "llama3.2:3b"


@app.route("/assist", methods=["POST"])
def assist():
    data = request.get_json()

    task = data.get("task", "unknown task")

    # Not used now
    frustration = data.get("frustration_score", 0.0)
    rage_clicks = data.get("rage_clicks", 0)

    page_context = data.get("page_context", {})
    trigger_reason = data.get("trigger_reason", "")
    trigger_score = data.get("trigger_score", 0.0)

    # Mouse input integration
    task_wrong_clicks = data.get("task_wrong_clicks", 0)
    form_errors = data.get("form_errors", 0)
    idle_time = data.get("idle_time", 0)
    mouse_status = data.get("mouse_status", "unknown")

    actions = data.get("recent_actions", [])
    user_message = data.get("user_message", "")
    mode = data.get("mode", "proactive")

    if mode == "chat" and user_message:
        prompt = f"""
    You are an on-screen usability assistant helping a user complete a web task.

    Current task: {task}
    Page name: {page_context.get("page_name", "unknown")}
    Page goal: {page_context.get("goal", "unknown")}
    Hint policy: {page_context.get("hint_policy", "Keep help short")}

    Trigger reason: {trigger_reason}
    Trigger score: {trigger_score}
    Wrong clicks on task: {task_wrong_clicks}
    Form errors: {form_errors}
    Idle time: {idle_time}
    Mouse status: {mouse_status}
    Recent actions: {actions}

    The user asked: {user_message}

    Answer the user's question directly.
    Use the page/task context.
    Keep it short and actionable.
    """
        print("LLM INPUT:", data)

    else:
        prompt = f"""
    You are an on-screen usability assistant helping a user complete a web task.

    Current task: {task}
    Page name: {page_context.get("page_name", "unknown")}
    Page goal: {page_context.get("goal", "unknown")}
    Hint policy: {page_context.get("hint_policy", "Keep help short")}

    Trigger reason: {trigger_reason}
    Trigger score: {trigger_score}
    Wrong clicks on task: {task_wrong_clicks}
    Form errors: {form_errors}
    Idle time: {idle_time}
    Mouse status: {mouse_status}
    Recent actions: {actions}

    Give one short proactive hint.
    Acknowledge the likely issue briefly.
    Do not mention internal scores.
    Speak directly to the user.
    """
        print("LLM INPUT:", data)

    ollama_response = requests.post(
        OLLAMA_URL, json={"model": MODEL, "prompt": prompt, "stream": False}, timeout=20
    )

    result = ollama_response.json()
    text = result.get("response", "").strip()

    return jsonify({"assistant_message": text, "source": "ollama"})


# Test LLM function, use only if server is down
# @app.route("/assist", methods=["POST"])
# def assist():
#     data = request.get_json()

#     task = data.get("task", "current task")
#     frustration = data.get("frustration_score", 0.0)
#     rage_clicks = data.get("rage_clicks", 0)
#     gaze = data.get("gaze_state", "unknown")
#     user_message = data.get("user_message")

#     if user_message:
#         msg = f"You asked: '{user_message}'. Based on your current task '{task}', try checking the required fields and error messages first."
#     elif frustration >= 0.7 or rage_clicks >= 3:
#         msg = (
#             f"It looks like you may be stuck on '{task}'. "
#             f"I noticed repeated interactions and elevated frustration. "
#             f"Try checking the highlighted fields or the current validation message."
#         )
#     else:
#         msg = (
#             f"You are currently on '{task}'. Let me know what part you need help with."
#         )

#     return jsonify(
#         {
#             "assistant_message": msg,
#             "suggested_action": "check_validation_message",
#             "source": "simulated_cloud_llm",
#         }
#     )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5001, debug=True)
