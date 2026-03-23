import sys
import json
import argparse
import time
import logging
from PySide6.QtCore import Signal, QObject
from PySide6.QtWidgets import (
    QApplication,
    QWidget,
    QTextEdit,
    QGridLayout,
    QLabel,
)
import paho.mqtt.client as mqtt

from stream_config import default_stream_args, parse_streams
from firebase_client import FirebaseClient

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Reconnect config
# ---------------------------------------------------------------------------
RECONNECT_DELAY_MIN = 1    # seconds before first reconnect attempt
RECONNECT_DELAY_MAX = 30   # seconds max backoff


class MqttSignals(QObject):
    message_received  = Signal(str, str)   # port_label, pretty_payload
    summary_received  = Signal(str, dict)  # port_label, summary_dict
    connection_status = Signal(str)        # status string for UI


class InsightPanel(QTextEdit):
    def __init__(self, title: str):
        super().__init__()
        self.setReadOnly(True)
        self.setText(f"{title}\nWaiting for MQTT updates...")
        self.setStyleSheet("""
            background-color: #111;
            color: #00ff99;
            border: 2px solid #444;
            font-size: 14px;
        """)


class Dashboard(QWidget):
    def __init__(
        self,
        streams: list[tuple[int, str]],
        firebase_clients: dict[str, FirebaseClient],
    ):
        super().__init__()
        self.setWindowTitle("MQTT Insights Dashboard")
        self.resize(1000, 400)
        self.firebase_clients = firebase_clients

        layout = QGridLayout()
        layout.setSpacing(10)

        # Status bar at top
        self.status_label = QLabel("MQTT: connecting...")
        self.status_label.setStyleSheet("color: #aaa; font-size: 12px; padding: 4px;")
        layout.addWidget(self.status_label, 0, 0, 1, 2)

        self.panels: dict[str, InsightPanel] = {}
        self.panel_titles: dict[str, str] = {}

        for index, (port, label) in enumerate(streams):
            panel_title = f"{label} (Port {port})"
            panel = InsightPanel(panel_title)
            row = (index // 2) + 1   # +1 to leave row 0 for status bar
            col = index % 2
            layout.addWidget(panel, row, col)
            self.panels[str(port)] = panel
            self.panel_titles[str(port)] = panel_title

        self.setLayout(layout)

    def position_bottom_center(self):
        screen = QApplication.primaryScreen()
        if screen is None:
            return

        available = screen.availableGeometry()
        x_pos = available.x() + (available.width() - self.width()) // 2
        y_pos = available.y() + available.height() - self.height() - 20
        self.move(max(available.x(), x_pos), max(available.y(), y_pos))

    def showEvent(self, event):
        super().showEvent(event)
        self.position_bottom_center()

    def update_panel(self, port_label: str, payload: str):
        panel = self.panels.get(port_label)
        if panel is not None:
            panel_title = self.panel_titles.get(port_label, f"Port {port_label}")
            panel.setText(f"{panel_title}\n\n{payload}")

    def handle_summary(self, port_label: str, summary: dict):
        """Called when a /summary MQTT message arrives for a stream."""
        panel = self.panels.get(port_label)
        if panel is not None:
            panel_title = self.panel_titles.get(port_label, f"Port {port_label}")
            pretty = json.dumps(summary, indent=2)
            panel.setText(f"{panel_title}\n\n[SESSION COMPLETE]\n\n{pretty}")

        # Push to Firebase
        client = self.firebase_clients.get(port_label)
        if client:
            client.push_summary(summary)
            logger.info(f"[Dashboard] Summary pushed to Firebase for port {port_label}")

    def set_connection_status(self, status: str):
        self.status_label.setText(f"MQTT: {status}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="MQTT dashboard for stream labels")
    parser.add_argument("--broker",        default="127.0.0.1")
    parser.add_argument("--broker-port",   type=int, default=1883)
    parser.add_argument("--raw-topic",     default="uat/sensor")
    parser.add_argument("--summary-topic", default="uat/summary")
    parser.add_argument("--streams",       nargs="+", default=default_stream_args())
    return parser.parse_args()


def main():
    logging.basicConfig(level=logging.INFO)
    args = parse_args()

    try:
        streams = parse_streams(args.streams)
    except ValueError as error:
        print(f"Error: {error}", file=sys.stderr)
        sys.exit(2)

    known_labels = {str(port) for port, _ in streams}

    # Create one FirebaseClient per stream, start sessions
    firebase_clients: dict[str, FirebaseClient] = {}
    for port, label in streams:
        firebase_clients[str(port)] = FirebaseClient(computer_id=label)

    app = QApplication(sys.argv)
    window = Dashboard(streams, firebase_clients)

    signals = MqttSignals()
    signals.message_received.connect(window.update_panel)
    signals.summary_received.connect(window.handle_summary)
    signals.connection_status.connect(window.set_connection_status)

    # ------------------------------------------------------------------
    # MQTT setup with reconnect
    # ------------------------------------------------------------------
    mqtt_client = mqtt.Client()
    mqtt_client.reconnect_delay_set(
        min_delay=RECONNECT_DELAY_MIN,
        max_delay=RECONNECT_DELAY_MAX,
    )

    def on_connect(client, userdata, flags, rc):
        if rc == 0:
            logger.info("[MQTT] Connected to broker")
            signals.connection_status.emit("connected")

            # Re-subscribe on every connect (handles reconnects)
            client.subscribe(args.raw_topic)
            client.subscribe(args.summary_topic)
            logger.info(f"[MQTT] Subscribed to {args.raw_topic} and {args.summary_topic}")
        else:
            error_map = {
                1: "incorrect protocol version",
                2: "invalid client ID",
                3: "broker unavailable",
                4: "bad credentials",
                5: "not authorised",
            }
            reason = error_map.get(rc, f"unknown error (rc={rc})")
            logger.warning(f"[MQTT] Connection refused: {reason}")
            signals.connection_status.emit(f"refused — {reason}")

    def on_disconnect(client, userdata, rc):
        if rc == 0:
            logger.info("[MQTT] Cleanly disconnected")
            signals.connection_status.emit("disconnected")
        else:
            logger.warning(f"[MQTT] Unexpected disconnect (rc={rc}), retrying...")
            signals.connection_status.emit(f"reconnecting... (rc={rc})")
            # loop_start() handles automatic reconnect with the delay set above

    def on_message(client, userdata, msg):
        raw_payload = msg.payload.decode("utf-8", errors="replace")

        try:
            data = json.loads(raw_payload)
        except json.JSONDecodeError:
            logger.warning(f"[MQTT] Invalid JSON on {msg.topic}: {raw_payload[:80]}")
            return

        label = str(data.get("label", "")).strip()
        if label not in known_labels:
            logger.warning(f"[MQTT] Unknown label '{label}' in payload")
            return

        if msg.topic == args.summary_topic:
            # End-of-session summary from the Pi
            signals.summary_received.emit(label, data)

        else:
            # Regular analytics tick — push to Firebase
            client_fb = firebase_clients.get(label)
            if client_fb:
                client_fb.push(data)

            pretty = json.dumps(data, indent=2)
            signals.message_received.emit(label, pretty)

    mqtt_client.on_connect    = on_connect
    mqtt_client.on_disconnect = on_disconnect
    mqtt_client.on_message    = on_message

    # Initial connect — loop_start() will auto-reconnect on drops
    try:
        mqtt_client.connect(args.broker, args.broker_port, keepalive=60)
    except Exception as e:
        logger.warning(f"[MQTT] Initial connect failed: {e} — will retry")
        signals.connection_status.emit("broker unreachable, retrying...")

    mqtt_client.loop_start()

    window.show()

    try:
        sys.exit(app.exec())
    finally:
        mqtt_client.loop_stop()
        mqtt_client.disconnect()
        for client in firebase_clients.values():
            client.stop()


if __name__ == "__main__":
    main()