import paho.mqtt.client as mqtt
import sys 

import time
import random
import json

class MQTTConstants:
    BROKER_PORT = 1883
    TOPIC = "screen/raw"
    VALID_LABELS = {5000, 5002}

class MQTTClient:
    def __init__(self, broker_ip, label=None, topic=MQTTConstants.TOPIC):
        if not broker_ip or label is None:
            print("Arguments required: <BROKER_IP> <YOUR_LABEL>")
            sys.exit(1)
            
        self.broker_ip = broker_ip
        self.broker_port = MQTTConstants.BROKER_PORT
        self.label = label
        self.topic = topic
        self.client = mqtt.Client()
        self.client.on_connect = self.on_connect
        self.client.on_disconnect = self.on_disconnect
        self.client.reconnect_delay_set(min_delay=1, max_delay=5)
        self.connected = False
        self.setup()
    
    def setup(self):
        try:
            self.client.connect(self.broker_ip, self.broker_port, 10)
            self.client.loop_start()
        except Exception as e:
            print(f"Failed to connect to MQTT broker at {self.broker_ip}:{self.broker_port}")
            print(f"Error: {e}")
            raise
    
    def publish(self, payload, qos=0):
        if not self.connected:
            # Skipping publish because client is not connected
            return
        
        result = self.client.publish(topic=self.topic, payload=payload, qos=qos)
        if result.rc != mqtt.MQTT_ERR_SUCCESS:
            print(f"Failed to publish message to {self.topic}")
            print(f"Error code: {result.rc}")
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
    
    def on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            self.connected = True
        else:
            self.connected = False

    def on_disconnect(self, client, userdata, rc):
        self.connected = False

    def keep_running(self):
        if not self.connected:
            time.sleep(1)

def main():
    BROKER_IP = sys.argv[1]
    LABEL = int(sys.argv[2])

    mqtt_sender = MQTTClient(broker_ip=BROKER_IP, label=LABEL)

    while True:
        # Sending example
        payload = mqtt_sender.build_payload(LABEL)
        mqtt_sender.publish(payload)
        time.sleep(1)

# if __name__ == "__main__":
#     main()