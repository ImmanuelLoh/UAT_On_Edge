#Command: python mqtt_sender.py <BROKER_IP> <LABEL>
#Label: 5000 or 5002

import time
import random
import json
import sys
import paho.mqtt.client as mqtt

BROKER_PORT = 1883
TOPIC = "screen/raw"
VALID_LABELS = {"5000", "5002"}

if len(sys.argv) != 3:
    print("Usage: python mqtt_sender.py <BROKER_IP> <LABEL>")
    print("Example: python mqtt_sender.py 192.168.0.144 5000")
    sys.exit(1)

BROKER_IP = sys.argv[1]
LABEL = sys.argv[2]

if LABEL not in VALID_LABELS:
    print(f"Error: LABEL must be one of {sorted(VALID_LABELS)}")
    sys.exit(1)

client = mqtt.Client()

try:
    client.connect(BROKER_IP, BROKER_PORT, 10)
except Exception as e:
    print(f"Failed to connect to broker {BROKER_IP}:{BROKER_PORT}")
    print(f"Error: {e}")
    sys.exit(1)

print(f"Connected to broker at {BROKER_IP}:{BROKER_PORT}")
print(f"Publishing to {TOPIC} with label={LABEL}")

while True:
    data = {
        "label": LABEL,
        "timestamp": time.time(),
        "frustration_score": round(random.uniform(0, 1), 2),
        "rage_clicks": random.randint(0, 5),
        "mouse_speed": random.randint(50, 500),
        "attention": random.choice(["focused", "distracted"])
    }

    payload = json.dumps(data)
    result = client.publish(TOPIC, payload)

    print(f"Sent to {TOPIC}: {payload} | rc={result.rc}")
    time.sleep(1)