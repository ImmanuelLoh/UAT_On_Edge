from flask import Flask, request, jsonify

app = Flask(__name__)


@app.route("/assist", methods=["POST"])
def assist():
    data = request.get_json()

    task = data.get("task", "current task")
    frustration = data.get("frustration_score", 0.0)
    rage_clicks = data.get("rage_clicks", 0)
    gaze = data.get("gaze_state", "unknown")
    user_message = data.get("user_message")

    if user_message:
        msg = f"You asked: '{user_message}'. Based on your current task '{task}', try checking the required fields and error messages first."
    elif frustration >= 0.7 or rage_clicks >= 3:
        msg = (
            f"It looks like you may be stuck on '{task}'. "
            f"I noticed repeated interactions and elevated frustration. "
            f"Try checking the highlighted fields or the current validation message."
        )
    else:
        msg = (
            f"You are currently on '{task}'. Let me know what part you need help with."
        )

    return jsonify(
        {
            "assistant_message": msg,
            "suggested_action": "check_validation_message",
            "source": "simulated_cloud_llm",
        }
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5001, debug=True)
