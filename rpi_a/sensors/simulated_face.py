import random
import time


def get_face_state():
    return {
        "type": "face_state",
        "ts": time.time(),
        "frustration_score": round(random.choice([0.2, 0.3, 0.4, 0.8, 0.9]), 2),
        "gaze_state": random.choice(["focused", "looking_away", "looking_at_error"]),
    }
