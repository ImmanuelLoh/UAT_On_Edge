import random
import time


def get_task_state():
    return {
        "type": "task_state",
        "ts": time.time(),
        "task": random.choice(["login", "checkout", "profile_update", "search"]),
    }
