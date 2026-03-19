import paho.mqtt.client as mqtt
import sys 

import time
import random
import json

class MQTTConstants:
    BROKER_PORT = 1883
    TOPIC = "screen/raw"
    VALID_LABELS = {"5000", "5002"}

class MQTTClient:
    def __init__(self, broker_ip, broker_port=MQTTConstants.BROKER_PORT, topic=MQTTConstants.TOPIC):
        if not broker_ip:
            print("Error: BROKER_IP is required as the first argument")
            print("Usage: python MQTTClient.py <BROKER_IP> <LABEL>")
            sys.exit(1)

        if broker_port not in MQTTConstants.VALID_LABELS:
            print(f"Error: LABEL must be one of {sorted(MQTTConstants.VALID_LABELS)}")
            print("Usage: python MQTTClient.py <BROKER_IP> <LABEL>")
            sys.exit(1)
            
        self.broker_ip = broker_ip
        self.broker_port = broker_port
        self.topic = topic
        self.client = mqtt.Client()
        self.setup()
    
    def setup(self):
        try:
            self.client.connect(self.broker_ip, self.broker_port, 10)
            print(f"Connected to MQTT broker at {self.broker_ip}:{self.broker_port}")
        except Exception as e:
            print(f"Failed to connect to MQTT broker at {self.broker_ip}:{self.broker_port}")
            print(f"Error: {e}")
            raise
    
    def publish(self, payload, qos=0):
        result = self.client.publish(topic=self.topic, payload=payload, qos=qos)
        if result.rc != mqtt.MQTT_ERR_SUCCESS:
            print(f"Failed to publish message to {self.topic}")
            print(f"Error code: {result.rc}")
            raise Exception(f"MQTT publish error: {result.rc}")
        else:
            print(f"Published message to {self.topic}: {payload}")

    def build_payload(self, label):
        data = {
            "label": label,
            "timestamp": time.time(),
            "frustration_score": round(random.uniform(0, 1), 2),
            "rage_clicks": random.randint(0, 5),
            "mouse_speed": random.randint(50, 500),
            "attention": random.choice(["focused", "distracted"])
        }
        json_data = json.dumps(data)
        return json_data
    
def main():
    BROKER_IP = sys.argv[1]
    LABEL = sys.argv[2]

    mqtt_sender = MQTTClient(broker_ip=BROKER_IP)
    
    while True:
        # Sending example
        payload = mqtt_sender.build_payload(LABEL)
        mqtt_sender.publish(payload)
        time.sleep(1)

# if __name__ == "__main__":
#     main()