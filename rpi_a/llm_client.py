import requests
from config import LAPTOP_LLM_URL

def request_assistance(context_summary: dict, mode: str = "proactive") -> dict:
    payload = dict(context_summary)
    payload["mode"] = mode

    try:
        response = requests.post(LAPTOP_LLM_URL, json=payload, timeout=20)
        response.raise_for_status()
        data = response.json()
        data["llm_timeout"] = False
        return data
    
    except requests.Timeout:
        return {
            "assistant_message": "",
            "source": "timeout",
            "llm_timeout": True
        }
    
    except Exception as e:
        return {
            "assistant_message": "",
            "source": "fallback",
            "llm_timeout": True
        }