import random
import time


def get_mouse_events():
    now = time.time()

    patterns = [
        {"type": "click", "target": "submit-btn", "ts": now},
        {"type": "click", "target": "submit-btn", "ts": now + 0.1},
        {"type": "click", "target": "submit-btn", "ts": now + 0.2},
    ]

    normal = [
        {"type": "click", "target": random.choice(["menu", "next-btn", "field-email"]), "ts": now}
    ]

    return random.choice([patterns, normal, []])