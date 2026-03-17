import requests
from config import LAPTOP_LLM_URL

def request_assistance(context_summary: dict, mode: str = "proactive") -> dict:
    payload = dict(context_summary)
    payload["mode"] = mode

    try:
        response = requests.post(LAPTOP_LLM_URL, json=payload, timeout=20)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        return {
            "assistant_message": f"Assistant unavailable: {e}",
            "source": "fallback"
        }