import requests
from flask import Flask, request, jsonify

app = Flask(__name__)


OLLAMA_URL = "http://localhost:11434/api/generate"
MODEL = "llama3.2:3b"


@app.route("/assist", methods=["POST"])
def assist():
    data = request.get_json()

    task = data.get("task", "unknown task")
    frustration = data.get("frustration_score", 0.0)
    rage_clicks = data.get("rage_clicks", 0)
    actions = data.get("recent_actions", [])
    user_message = data.get("user_message", "")
    mode = data.get("mode", "proactive")

    if mode == "chat" and user_message:
        prompt = f"""
You are an on-screen usability assistant.

Current task: {task}
Recent actions: {actions}
User message: {user_message}

Reply directly to the user.
Answer their message first.
Do not say "the user".
Do not speak in third person.
Keep the reply short and practical.
"""
    else:
        prompt = f"""
You are an on-screen usability assistant.

Current task: {task}
Frustration score: {frustration}
Rage clicks: {rage_clicks}
Recent actions: {actions}

Speak directly to the user.
Do not say "the user".
Do not describe the user in third person.
Give one short helpful suggestion.
"""

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
