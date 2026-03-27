import paho.mqtt.client as mqtt
import sys 

import time
import random
import json

class MQTTConstants:
    BROKER_PORT = 1883
    RAW_TOPIC = "uat/raw"
    SUMMARY_TOPIC = "uat/summary"
    VALID_LABELS = {5000, 5002}

class MQTTClient:
    def __init__(self, broker_ip, label=None, raw_topic=MQTTConstants.RAW_TOPIC, summary_topic=MQTTConstants.SUMMARY_TOPIC):
        if not broker_ip or label is None:
            print("Arguments required: <BROKER_IP> <YOUR_LABEL>")
            sys.exit(1)
            
        self.broker_ip = broker_ip
        self.broker_port = MQTTConstants.BROKER_PORT
        self.label = label
        self.raw_topic = raw_topic
        self.summary_topic = summary_topic
        self.session_active = True
        self.client = mqtt.Client()
        self.client.on_connect = self.on_connect
        self.client.on_disconnect = self.on_disconnect
        self.client.reconnect_delay_set(min_delay=1, max_delay=5)
        self.connected = False
        self.setup()
    
    def setup(self):
        while True:
            try:
                print(f"[MQTTClient] Attempting to connect to {self.broker_ip}:{self.broker_port}")
                self.client.connect(self.broker_ip, self.broker_port, 10)
                self.client.loop_start()
                break
            except Exception as e:
                print(f"[MQTTClient] Initial connection failed: {e}")
                time.sleep(2)
    
    def publish_tick(self, payload, qos=0):
        if not self.connected:
            # Skipping publish because client is not connected
            return

        if not self.session_active:
            # Session has ended — suppress further raw publishes
            return
        
        result = self.client.publish(topic=self.raw_topic, payload=payload, qos=qos)
        if result.rc != mqtt.MQTT_ERR_SUCCESS:
            print(f"Failed to publish message to {self.raw_topic}")
            print(f"Error code: {result.rc}")
        else:
            print(f"Published message to {self.raw_topic}: {payload}")

    def publish_summary(self, summary_payload, qos=1):
        """Publish end-of-session summary to uat/summary and mark session inactive."""
        if not self.connected:
            print("[MQTTClient] Cannot publish summary — not connected.")
            return

        result = self.client.publish(
            topic=self.summary_topic,
            payload=summary_payload,
            qos=qos,
        )
        if result.rc != mqtt.MQTT_ERR_SUCCESS:
            print(f"[MQTTClient] Failed to publish summary. Error code: {result.rc}")
        else:
            print(f"[MQTTClient] Session summary published to {self.summary_topic}")

        # Mark session inactive — no more raw publishes after summary
        self.session_active = False
        print("[MQTTClient] Session marked inactive.")

    # def build_payload(self, label):
    #     data = {
    #         "label": label,
    #         "timestamp": time.time(),
    #         "frustration_score": round(random.uniform(0, 1), 2),
    #         "rage_clicks": random.randint(0, 5),
    #         "mouse_speed": random.randint(50, 500),
    #         "attention": random.choice(["focused", "distracted"])
    #     }
    #     json_data = json.dumps(data)
    #     return json_data
    
    def build_payload(self, label, sensor_state):
        data = {
            "label": label,
            "timestamp": sensor_state.get("timestamp"),
            
            "browser": {
                "task": sensor_state.get("browser", {}).get("task", "unknown"),
                "correct_click": sensor_state.get("browser", {}).get("correct_click", 0),
                "wrong_click": sensor_state.get("browser", {}).get("wrong_click", 0),
            },

            "mouse": {
                "idle_time": sensor_state.get("mouse", {}).get("idle_time", 0.0),
                "mouse_status": sensor_state.get("mouse", {}).get("mouse_status", "unknown"),
                "interval_clicks_per_second": sensor_state.get("mouse", {}).get("interval_clicks_per_second", 0.0),
                "overall_clicks_per_second": sensor_state.get("mouse", {}).get("overall_clicks_per_second", 0.0),
                "top_quadrant": sensor_state.get("mouse", {}).get("top_quadrant", "unknown"),
            },

            "face": {
                "face_detected": sensor_state.get("face", {}).get("face_detected", False),
                "frustration_score": sensor_state.get("face", {}).get("frustration_score", 0.0),
                "attention_score": sensor_state.get("face", {}).get("attention_score", 0.0),
                "emotion": sensor_state.get("face", {}).get("emotion", "N/A"),
                "direction": sensor_state.get("face", {}).get("direction", "N/A"),
                "gaze_quadrant": sensor_state.get("face", {}).get("gaze_quadrant", "NO_FACE"),
                "blink_rate": sensor_state.get("face", {}).get("blink_rate", 0.0),
                "avg_ear": sensor_state.get("face", {}).get("avg_ear", 0.0),
            },
            "llm": {
                "llm_activated": sensor_state.get("llm", {}).get("llm_activated", False),
                "last_role": sensor_state.get("llm", {}).get("last_role"),
                "last_message": sensor_state.get("llm", {}).get("last_message", ""),
                "llm_timeout": sensor_state.get("llm", {}).get("llm_timeout", False),
            },
            "alerts": {
                "frustration": sensor_state.get("alerts", {}).get("frustration", False)
            }
        }
        return json.dumps(data)
    
    def on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            self.connected = True
        else:
            self.connected = False

    def on_disconnect(self, client, userdata, rc):
        self.connected = False


def main():
    BROKER_IP = sys.argv[1]
    LABEL = int(sys.argv[2])

    mqtt_sender = MQTTClient(broker_ip=BROKER_IP, label=LABEL)

    while True:
        # Sending example
        payload = mqtt_sender.build_payload(LABEL)
        mqtt_sender.publish_tick(payload)
        time.sleep(1)

# if __name__ == "__main__":
#     main()