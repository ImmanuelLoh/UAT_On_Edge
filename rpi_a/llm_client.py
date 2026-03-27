import requests
from config import LAPTOP_LLM_URL

# For profiling
import time
import requests

def request_assistance(context_summary: dict, mode: str = "proactive") -> dict:
    payload = dict(context_summary)
    payload["mode"] = mode

    try:
        start = time.perf_counter()
        
        response = requests.post(LAPTOP_LLM_URL, json=payload, timeout=20)
        
        elapsed_ms = (time.perf_counter() - start) * 1000.0

        print(f"[LLM CLIENT PROFILE] mode={mode} http_total_ms={elapsed_ms:.2f}")
        
        response.raise_for_status()
        data = response.json()
        data["llm_timeout"] = False
        return data
    
    except requests.Timeout:
        return {
            "assistant_message": "Assistant timed out.",
            "source": "timeout",
            "llm_timeout": True
        }
    
    except Exception as e:
        return {
            "assistant_message": f"Assistant unavailable: {e}",
            "source": "fallback",
            "llm_timeout": True
        }