import sys
import json
import argparse
from PySide6.QtCore import Signal, QObject
from PySide6.QtWidgets import (
    QApplication,
    QWidget,
    QTextEdit,
    QGridLayout,
)
import paho.mqtt.client as mqtt

from stream_config import default_stream_args, parse_streams


class MqttSignals(QObject):
    message_received = Signal(str, str)


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
    def __init__(self, streams: list[tuple[int, str]]):
        super().__init__()
        self.setWindowTitle("MQTT Insights Dashboard")
        self.resize(1000, 400)

        layout = QGridLayout()
        layout.setSpacing(10)

        self.panels: dict[str, InsightPanel] = {}
        self.panel_titles: dict[str, str] = {}
        for index, (port, label) in enumerate(streams):
            panel_title = f"{label} (Port {port})"
            panel = InsightPanel(panel_title)
            row = index // 2
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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="MQTT dashboard for stream labels")
    parser.add_argument("--broker", default="127.0.0.1")
    parser.add_argument("--broker-port", type=int, default=1883)
    parser.add_argument("--raw-topic", default="screen/raw")
    parser.add_argument("--streams", nargs="+", default=default_stream_args())
    return parser.parse_args()


def main():
    args = parse_args()
    try:
        streams = parse_streams(args.streams)
    except ValueError as error:
        print(f"Error: {error}", file=sys.stderr)
        sys.exit(2)
    known_labels = {str(port) for port, _ in streams}

    app = QApplication(sys.argv)
    window = Dashboard(streams)

    signals = MqttSignals()
    signals.message_received.connect(window.update_panel)

    client = mqtt.Client()

    def on_connect(client, userdata, flags, rc):
        print(f"Connected to MQTT broker with result code {rc}")
        client.subscribe(args.raw_topic)
        print(f"Subscribed to {args.raw_topic}")

    def on_message(client, userdata, msg):
        raw_payload = msg.payload.decode("utf-8", errors="replace")

        try:
            data = json.loads(raw_payload)
        except json.JSONDecodeError:
            print(f"Invalid JSON received: {raw_payload}")
            return

        label = str(data.get("label", "")).strip()
        if label not in known_labels:
            print(f"Unknown label '{label}' in payload: {raw_payload}")
            return

        pretty_payload = json.dumps(data, indent=2)
        signals.message_received.emit(label, pretty_payload)

    client.on_connect = on_connect
    client.on_message = on_message

    client.connect(args.broker, args.broker_port, 60)
    client.loop_start()

    window.show()

    try:
        sys.exit(app.exec())
    finally:
        client.loop_stop()
        client.disconnect()


if __name__ == "__main__":
    main()