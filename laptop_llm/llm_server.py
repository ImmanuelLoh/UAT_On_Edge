import requests
from flask import Flask, request, jsonify

# For profiling
import time
from rpi_a.profiler_utils import RollingProfiler, Timer
profiler = RollingProfiler(max_samples=1000)

app = Flask(__name__)


OLLAMA_URL = "http://localhost:11434/api/generate"
MODEL = "llama3.2:3b"


@app.route("/assist", methods=["POST"])
def assist():
    overall_start = time.perf_counter()
    profiler.incr("llm.assist.calls")
    
    with Timer(profiler, "llm.request.get_json"):
        data = request.get_json()

    request_id = data.get("request_id", "no-request-id")

    with Timer(profiler, "llm.payload.extract_fields"):
        task = data.get("task", "unknown task")

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

        allowed_elements = page_context.get("allowed_elements", [])
        forbidden_inferences = page_context.get("forbidden_inferences", [])
        instruction_text = page_context.get("instruction_text", "")
        visible_elements = page_context.get("visible_elements", [])

    with Timer(profiler, "llm.prompt.build"):
        if mode == "chat" and user_message:
            prompt = f"""
        You are an on-screen usability assistant for a controlled user test.

        Current task: {task}
        Page name: {page_context.get("page_name", "unknown")}
        Page goal: {page_context.get("goal", "unknown")}
        Instruction text on page: {instruction_text}
        Visible elements on page: {visible_elements}
        Allowed UI elements: {allowed_elements}
        Do not mention or invent: {forbidden_inferences}

        User behaviour:
        - Trigger reason: {trigger_reason}
        - Wrong clicks on task: {task_wrong_clicks}
        - Form errors: {form_errors}
        - Idle time: {idle_time}
        - Mouse status: {mouse_status}
        - Recent actions: {actions}

        The user asked: {user_message}

        Rules:
        - Answer using only the provided page context and visible elements.
        - You may restate the task in simpler words.
        - You may list the visible options if the user asks what the options are.
        - You may mention position only if it is explicitly provided in the visible elements.
        - Do not invent UI elements, buttons, icons, controls, colors, or steps not explicitly provided.
        - Never mention scores, metrics, signals, or internal analysis.
        - Keep the answer short, clear, and specific.
        - Answer in 1 or 2 short sentences.
        - For number tiles, refer to choices only by their number labels.
        - Never use spatial descriptions like top, bottom, left, right, first, last, or middle unless the page context explicitly provides positions.

        Return only the assistant message.
        """
        else:
            prompt = f"""
        You are an on-screen usability assistant for a controlled user test.

        Current task: {task}
        Page name: {page_context.get("page_name", "unknown")}
        Page goal: {page_context.get("goal", "unknown")}
        Instruction text on page: {instruction_text}
        Visible elements on page: {visible_elements}
        Allowed UI elements: {allowed_elements}
        Do not mention or invent: {forbidden_inferences}

        User behaviour:
        - Trigger reason: {trigger_reason}
        - Wrong clicks on task: {task_wrong_clicks}
        - Form errors: {form_errors}
        - Idle time: {idle_time}
        - Mouse status: {mouse_status}
        - Recent actions: {actions}

        Rules:
        - Give exactly one concrete next step.
        - Only mention elements explicitly listed in the page context.
        - Do not invent buttons, controls, forms, menus, checkmarks, shades, or hidden elements.
        - Do not mention internal reasoning, scores, metrics, signals, or observations.
        - Do not explain why the message is being shown.
        - Keep the response to one short sentence whenever possible.
        - For number tiles, refer to choices only by their number labels.
        - Never use spatial descriptions like top, bottom, left, right, first, last, or middle unless the page context explicitly provides positions.

        Return only the assistant message.
        """

    try:
        with Timer(profiler, "llm.ollama.post"):
            ollama_response = requests.post(
                OLLAMA_URL,
                json={
                    "model": MODEL,
                    "prompt": prompt,
                    "stream": False,
                    "options": {"temperature": 0.1, "top_p": 0.6},
                },
                timeout=20,
            )

        with Timer(profiler, "llm.ollama.json_decode"):
            result = ollama_response.json()

        with Timer(profiler, "llm.response.extract"):
            assistant_text = result.get("response", "").strip()
            if not assistant_text:
                assistant_text = "Try reviewing the task instructions again."

        total_ms = (time.perf_counter() - overall_start) * 1000.0
        profiler.record_ms("llm.assist.total", total_ms)

        print(
            f"[LLM PROFILE] request_id={request_id} mode={mode} "
            f"total_ms={total_ms:.2f}"
        )

        return jsonify({"assistant_message": assistant_text}), 200

    except Exception as e:
        total_ms = (time.perf_counter() - overall_start) * 1000.0
        profiler.record_ms("llm.assist.total_error", total_ms)
        import traceback
        traceback.print_exc()
        return jsonify({
            "assistant_message": "LLM response error.",
            "error": str(e)
        }), 500

@app.route("/profiler_stats", methods=["GET"])
def profiler_stats():
    return jsonify(profiler.snapshot())
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
