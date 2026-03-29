import paho.mqtt.client as mqtt
import sys

import time
import json

REPLAY_FRAGMENT_SIZE = 120       # ticks per fragment (120 * 0.5s = 1 minute of data)
REPLAY_INTER_MSG_DELAY = 0.05   # seconds between fragments to avoid broker flooding


class MQTTConstants:
    BROKER_PORT = 1883
    RAW_TOPIC = "uat/raw"
    SUMMARY_TOPIC = "uat/summary"
    REPLAY_TOPIC = "uat/replay"
    VALID_LABELS = {5000, 5002}

class MQTTClient:
    def __init__(self, broker_ip, label=None, raw_topic=MQTTConstants.RAW_TOPIC,
                 summary_topic=MQTTConstants.SUMMARY_TOPIC,
                 replay_topic=MQTTConstants.REPLAY_TOPIC):
        if not broker_ip or label is None:
            print("Arguments required: <BROKER_IP> <YOUR_LABEL>")
            sys.exit(1)
            
        self.broker_ip = broker_ip
        self.broker_port = MQTTConstants.BROKER_PORT
        self.label = label
        self.raw_topic = raw_topic
        self.summary_topic = summary_topic
        self.replay_topic = replay_topic
        self.session_active = True
        self.client = mqtt.Client()
        self.client.on_connect = self.on_connect
        self.client.on_disconnect = self.on_disconnect
        self.client.reconnect_delay_set(min_delay=1, max_delay=5)
        self.connected = False
        self.setup()
    
    def setup(self):
        try:
            print(f"[MQTTClient] Attempting to connect to {self.broker_ip}:{self.broker_port}")
            self.client.connect(self.broker_ip, self.broker_port, 10)
        except Exception as e:
            print(f"[MQTTClient] Initial connection failed: {e} — will retry in background")
        self.client.loop_start()
    
    def publish_tick(self, payload, qos=0):
        if not self.connected:
            return
        if not self.session_active:
            return

        result = self.client.publish(topic=self.raw_topic, payload=payload, qos=qos)
        if result.rc != mqtt.MQTT_ERR_SUCCESS:
            print(f"Failed to publish message to {self.raw_topic}, error code: {result.rc}")
        else:
            print(f"Published message to {self.raw_topic}: {payload}")

    def publish_summary(self, summary_payload, qos=1):
        # Mark session inactive — no more raw publishes after summary
        self.session_active = False
        print("[MQTTClient] Session marked inactive.")

        if not self.connected:
            print("[MQTTClient] Cannot publish summary, waiting for reconnect.")
            if not self._wait_for_connection(timeout=30):
                print("[MQTTClient] Reconnect failed. Summary not published.")
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



    def publish_replay(self, session_id: str, snapshots: list,
                       label: int, fragment_size: int = REPLAY_FRAGMENT_SIZE,
                       qos: int = 1) -> int:
        """
        Slice *snapshots* into fixed-size fragments and publish each one to
        uat/replay with QoS 1.

        Each message has the shape:
            {
                "label":       5000,
                "session_id":  "2026-03-27_14-00-00",
                "seq":         0,          # 0-based fragment index
                "total":       12,         # total number of fragments
                "ticks":       [ ... ]     # up to fragment_size snapshots
            }

        Returns the total number of fragments sent.
        """
        if not self.connected:
            print("[MQTTClient] Cannot publish replay, waiting for reconnect.")
            if not self._wait_for_connection(timeout=30):
                print("[MQTTClient] Reconnect failed. Replay not published.")
            return 0

        chunks = [
            snapshots[i: i + fragment_size]
            for i in range(0, len(snapshots), fragment_size)
        ]
        total = len(chunks)

        print(f"[MQTTClient] Publishing replay: {len(snapshots)} ticks "
              f"→ {total} fragments (size={fragment_size}) for session {session_id}")

        for seq, chunk in enumerate(chunks):
            fragment = {
                "label":      label,
                "session_id": session_id,
                "seq":        seq,
                "total":      total,
                "ticks":      chunk,
            }
            payload = json.dumps(fragment)

            # Wait for the broker's ACK before the next fragment so we don't
            # overflow the in-flight window on a congested link
            info = self.client.publish(
                topic=self.replay_topic,
                payload=payload,
                qos=qos,
            )
            info.wait_for_publish(timeout=10)

            if info.rc != mqtt.MQTT_ERR_SUCCESS:
                print(f"[MQTTClient] Replay fragment {seq}/{total} failed "
                      f"(rc={info.rc})")
            else:
                print(f"[MQTTClient] Replay fragment {seq + 1}/{total} delivered")

            time.sleep(REPLAY_INTER_MSG_DELAY)

        print(f"[MQTTClient] Replay complete for session {session_id}")
        return total

    def build_payload(self, label, sensor_state, session_id):
        data = {
            "label": label,
            "timestamp": sensor_state.get("timestamp"),
            "session_id": session_id,
            
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
                "llm_timeout": sensor_state.get("llm", {}).get("llm_timeout", False)
            },
            "alerts": {
                "frustration": sensor_state.get("alerts", {}).get("frustration", False),
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
    
    def _wait_for_connection(self, timeout=30) -> bool:
        """Wait up to N seconds for paho to reconnect. Returns True if connected."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.connected:
                return True
            time.sleep(1)
        return False