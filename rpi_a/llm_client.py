import requests
from config import LAPTOP_LLM_URL


def request_assistance(context_summary: dict) -> dict:
    try:
        response = requests.post(LAPTOP_LLM_URL, json=context_summary, timeout=10)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        return {
            "assistant_message": f"Assistant unavailable: {str(e)}",
            "suggested_action": None,
            "source": "fallback",
        }
