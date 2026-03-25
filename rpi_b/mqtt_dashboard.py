import sys
import json
import argparse
import logging
from PySide6.QtCore import Signal, QObject, Qt
from PySide6.QtGui import QFont, QColor, QPalette
from PySide6.QtWidgets import (
    QApplication,
    QWidget,
    QGridLayout,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QFrame,
    QScrollArea,
    QSizePolicy,
)
import paho.mqtt.client as mqtt

from stream_config import default_stream_args, parse_streams
from firebase_client import FirebaseClient

logger = logging.getLogger(__name__)

RECONNECT_DELAY_MIN = 1
RECONNECT_DELAY_MAX = 30

# ---------------------------------------------------------------------------
# Data parser
# ---------------------------------------------------------------------------

def parse_mqtt_payload(data: dict) -> dict | None:
    """
    Parse a raw MQTT JSON payload into structured display fields.
    Returns None if parsing fails or required keys are missing.
    """
    try:
        browser = data.get("browser", {})
        mouse   = data.get("mouse", {})
        face    = data.get("face", {})
        llm     = data.get("llm", {})

        return {
            "task":                    browser.get("task", "—"),
            "correct_click":           browser.get("correct_click", "—"),
            "wrong_click":             browser.get("wrong_click", "—"),
            "mouse_status":            mouse.get("mouse_status", "—"),
            "idle_time":               mouse.get("idle_time", "—"),
            "clicks_per_second":       mouse.get("overall_clicks_per_second", "—"),
            "top_quadrant":            mouse.get("top_quadrant") or "—",
            "face_detected":           face.get("face_detected", "—"),
            "emotion":                 face.get("emotion", "—"),
            "frustration_score":       face.get("frustration_score", "—"),
            "attention_score":         face.get("attention_score", "—"),
            "direction":               face.get("direction", "—"),
            "gaze_quadrant":           face.get("gaze_quadrant", "—"),
            "blink_rate":              face.get("blink_rate", "—"),
            "llm_activated":           llm.get("llm_activated", False),
            "llm_last_role":           llm.get("last_role"),
            "llm_last_message":        llm.get("last_message", ""),
        }
    except Exception as e:
        logger.warning(f"[Parser] Failed to parse payload: {e}")
        return None


# ---------------------------------------------------------------------------
# Signals
# ---------------------------------------------------------------------------

class MqttSignals(QObject):
    message_received  = Signal(str, str)   # port_label, pretty_payload (fallback)
    parsed_received   = Signal(str, dict)  # port_label, parsed_dict
    summary_received  = Signal(str, dict)  # port_label, summary_dict
    connection_status = Signal(str)


# ---------------------------------------------------------------------------
# UI helpers
# ---------------------------------------------------------------------------

LIGHT_BG       = "#F7F7F5"
CARD_BG        = "#FFFFFF"
BORDER         = "#E2E2DF"
TEXT_PRIMARY   = "#1A1A1A"
TEXT_SECONDARY = "#6B6B6B"
TEXT_MUTED     = "#9E9E9E"
ACCENT_BLUE    = "#2563EB"
ACCENT_GREEN   = "#16A34A"
ACCENT_RED     = "#DC2626"
ACCENT_AMBER   = "#D97706"

FONT_MONO   = "JetBrains Mono, Menlo, Consolas, monospace"
FONT_SANS   = "SF Pro Text, Segoe UI, Helvetica Neue, sans-serif"
FONT_TITLE  = "SF Pro Display, Segoe UI Semibold, Helvetica Neue, sans-serif"


def _label(text: str, style: str = "") -> QLabel:
    lbl = QLabel(text)
    if style:
        lbl.setStyleSheet(style)
    return lbl


def emotion_color(emotion: str) -> str:
    emotion = str(emotion).upper()
    mapping = {
        "FRUSTRATED": ACCENT_RED,
        "HAPPY":      ACCENT_GREEN,
        "NEUTRAL":    TEXT_SECONDARY,
        "SAD":        ACCENT_BLUE,
        "ANGRY":      ACCENT_RED,
        "SURPRISED":  ACCENT_AMBER,
    }
    return mapping.get(emotion, TEXT_PRIMARY)


def score_color(score) -> str:
    try:
        v = float(score)
        if v >= 70:
            return ACCENT_RED
        if v >= 40:
            return ACCENT_AMBER
        return ACCENT_GREEN
    except (TypeError, ValueError):
        return TEXT_PRIMARY


class Divider(QFrame):
    def __init__(self):
        super().__init__()
        self.setFrameShape(QFrame.Shape.HLine)
        self.setStyleSheet(f"color: {BORDER}; background: {BORDER}; border: none; max-height: 1px;")


class SectionHeader(QLabel):
    def __init__(self, text: str):
        super().__init__(text.upper())
        self.setStyleSheet(f"""
            color: {TEXT_MUTED};
            font-size: 10px;
            font-weight: 600;
            letter-spacing: 1.2px;
            font-family: {FONT_SANS};
            padding: 2px 0;
        """)


class DataRow(QWidget):
    """A single key-value row."""
    def __init__(self, key: str, value: str, value_color: str = TEXT_PRIMARY):
        super().__init__()
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 2, 0, 2)
        layout.setSpacing(8)

        key_lbl = QLabel(key)
        key_lbl.setStyleSheet(f"""
            color: {TEXT_SECONDARY};
            font-size: 12px;
            font-family: {FONT_SANS};
            min-width: 160px;
        """)
        key_lbl.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred)

        val_lbl = QLabel(str(value))
        val_lbl.setStyleSheet(f"""
            color: {value_color};
            font-size: 13px;
            font-weight: 600;
            font-family: {FONT_SANS};
        """)
        val_lbl.setWordWrap(True)

        layout.addWidget(key_lbl)
        layout.addWidget(val_lbl, 1)


class InsightPanel(QWidget):
    """
    A card that displays parsed MQTT data or falls back to raw JSON.
    """
    def __init__(self, title: str):
        super().__init__()
        self._title = title
        self._llm_activated = False # LLM latest persist across ticks
        self._llm_last_role = None # LLM latest persist across ticks
        self._llm_last_message = "" # LLM latest persist across ticks
        self._build_ui()

    def _build_ui(self):
        self.setObjectName("InsightPanel")
        self.setStyleSheet(f"""
            #InsightPanel {{
                background: {CARD_BG};
                border: 1px solid {BORDER};
                border-radius: 10px;
            }}
        """)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        # Outer layout holds the fixed header + scrollable body
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 14, 16, 14)
        outer.setSpacing(8)

        # Header
        header_row = QHBoxLayout()
        header_row.setContentsMargins(0, 0, 0, 0)

        title_lbl = QLabel(self._title)
        title_lbl.setStyleSheet(f"""
            color: {TEXT_PRIMARY};
            font-size: 14px;
            font-weight: 700;
            font-family: {FONT_TITLE};
        """)

        self._dot = QLabel("●")
        self._dot.setStyleSheet(f"color: {TEXT_MUTED}; font-size: 10px;")

        header_row.addWidget(title_lbl)
        header_row.addStretch()
        header_row.addWidget(self._dot)

        outer.addLayout(header_row)
        outer.addWidget(Divider())

        # Scrollable content area
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setStyleSheet("background: transparent;")

        self._content_widget = QWidget()
        self._content_widget.setStyleSheet("background: transparent;")
        self._content_layout = QVBoxLayout(self._content_widget)
        self._content_layout.setContentsMargins(0, 4, 0, 4)
        self._content_layout.setSpacing(2)
        self._content_layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        # Default placeholder
        placeholder = QLabel("Waiting for MQTT data…")
        placeholder.setStyleSheet(f"color: {TEXT_MUTED}; font-size: 12px; font-family: {FONT_SANS};")
        self._content_layout.addWidget(placeholder)

        self._scroll.setWidget(self._content_widget)
        outer.addWidget(self._scroll, 1)

    def _clear_content(self):
        while self._content_layout.count():
            item = self._content_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def set_status_dot(self, color: str):
        self._dot.setStyleSheet(f"color: {color}; font-size: 10px;")

    def update_parsed(self, parsed: dict):
        # Update cached LLM state — only overwrite message if a new one arrived
        if parsed.get("llm_activated"):
            self._llm_activated = True

        incoming_message = parsed.get("llm_last_message", "")
        if incoming_message:
            self._llm_last_message = incoming_message
            self._llm_last_role = parsed.get("llm_last_role")

        # Reset cache when task changes (llm_activated came in as False)
        if not parsed.get("llm_activated") and not incoming_message:
            # Only reset if it was previously activated — avoids resetting on startup
            if self._llm_activated:
                self._llm_activated = False
                self._llm_last_role = None
                self._llm_last_message = ""

        # Render structured data as labelled rows
        self._clear_content()
        self.set_status_dot(ACCENT_GREEN)
        layout = self._content_layout

        # ── Task ──────────────────────────────────────────────
        layout.addWidget(SectionHeader("Task"))
        layout.addWidget(DataRow("Current Task", str(parsed["task"])))
        layout.addWidget(DataRow("Correct Clicks", str(parsed["correct_click"])))
        layout.addWidget(DataRow("Wrong Clicks",   str(parsed["wrong_click"])))
        layout.addSpacing(6)
        layout.addWidget(Divider())
        layout.addSpacing(4)

        # ── Mouse ─────────────────────────────────────────────
        layout.addWidget(SectionHeader("Mouse"))
        mouse_color = ACCENT_GREEN if str(parsed["mouse_status"]).upper() == "ACTIVE" else ACCENT_AMBER
        layout.addWidget(DataRow("Status",         str(parsed["mouse_status"]), mouse_color))
        layout.addWidget(DataRow("Idle Time (s)",  str(parsed["idle_time"])))
        layout.addWidget(DataRow("Clicks / sec",   str(parsed["clicks_per_second"])))
        layout.addWidget(DataRow("Top Quadrant",   str(parsed["top_quadrant"])))
        layout.addSpacing(6)
        layout.addWidget(Divider())
        layout.addSpacing(4)

        # ── Face ──────────────────────────────────────────────
        layout.addWidget(SectionHeader("Face & Gaze"))
        face_color = ACCENT_GREEN if str(parsed["face_detected"]).lower() == "true" else ACCENT_RED
        layout.addWidget(DataRow("Face Detected",    str(parsed["face_detected"]), face_color))
        layout.addWidget(DataRow("Emotion",           str(parsed["emotion"]),
                                 emotion_color(str(parsed["emotion"]))))
        layout.addWidget(DataRow("Frustration Score", f'{parsed["frustration_score"]}',
                                 score_color(parsed["frustration_score"])))
        layout.addWidget(DataRow("Attention Score",   f'{parsed["attention_score"]}'))
        layout.addWidget(DataRow("Direction",         str(parsed["direction"])))
        layout.addWidget(DataRow("Gaze Quadrant",     str(parsed["gaze_quadrant"])))
        layout.addWidget(DataRow("Blink Rate (bpm)",  str(parsed["blink_rate"])))

        # ── LLM Assistant ─────────────────────────────────────
        layout.addSpacing(6)
        layout.addWidget(Divider())
        layout.addSpacing(4)
        layout.addWidget(SectionHeader("LLM Assistant"))

        if self._llm_activated:
            layout.addWidget(DataRow("Status", "Activated", ACCENT_GREEN))
            if self._llm_last_message:
                role_label = "Assistant" if self._llm_last_role == "assistant" else "User"
                role_color = ACCENT_BLUE if self._llm_last_role == "assistant" else TEXT_PRIMARY
                layout.addWidget(DataRow("Last Speaker", role_label, role_color))

                # Message bubble
                msg_lbl = QLabel(self._llm_last_message)
                msg_lbl.setWordWrap(True)
                msg_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
                msg_lbl.setStyleSheet(f"""
                    color: {TEXT_PRIMARY};
                    font-size: 12px;
                    font-family: {FONT_SANS};
                    background: {LIGHT_BG};
                    border-left: 3px solid {role_color};
                    border-radius: 4px;
                    padding: 6px 8px;
                """)
                layout.addWidget(msg_lbl)
        else:
            layout.addWidget(DataRow("Status", "Not activated", TEXT_MUTED))

        layout.addStretch()
        
    def update_raw(self, raw_json: str):
        """Fallback: render raw JSON in a monospace label."""
        self._clear_content()
        self.set_status_dot(ACCENT_AMBER)
        lbl = QLabel(raw_json)
        lbl.setStyleSheet(f"""
            color: {TEXT_SECONDARY};
            font-size: 11px;
            font-family: {FONT_MONO};
            background: {LIGHT_BG};
            border-radius: 6px;
            padding: 8px;
        """)
        lbl.setWordWrap(True)
        lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._content_layout.addWidget(lbl)

    def update_summary(self, summary: dict):
        """Display a session-complete summary."""
        self._clear_content()
        self.set_status_dot(ACCENT_BLUE)
        header = QLabel("SESSION COMPLETE")
        header.setStyleSheet(f"""
            color: {ACCENT_BLUE};
            font-size: 11px;
            font-weight: 700;
            letter-spacing: 1px;
            font-family: {FONT_SANS};
        """)
        self._content_layout.addWidget(header)
        self._content_layout.addSpacing(6)

        pretty = json.dumps(summary, indent=2)
        lbl = QLabel(pretty)
        lbl.setStyleSheet(f"""
            color: {TEXT_SECONDARY};
            font-size: 11px;
            font-family: {FONT_MONO};
            background: {LIGHT_BG};
            border-radius: 6px;
            padding: 8px;
        """)
        lbl.setWordWrap(True)
        lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._content_layout.addWidget(lbl)


# ---------------------------------------------------------------------------
# Dashboard window
# ---------------------------------------------------------------------------

class Dashboard(QWidget):
    def __init__(
        self,
        streams: list[tuple[int, str]],
        firebase_clients: dict[str, FirebaseClient],
    ):
        super().__init__()
        self.setWindowTitle("MQTT Insights Dashboard")
        self.firebase_clients = firebase_clients

        # Light palette
        palette = self.palette()
        palette.setColor(QPalette.ColorRole.Window, QColor(LIGHT_BG))
        self.setPalette(palette)
        self.setAutoFillBackground(True)

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 16)
        root.setSpacing(10)

        # ── Top bar ──────────────────────────────────────────
        top_bar = QHBoxLayout()

        brand = QLabel("MQTT Insights")
        brand.setStyleSheet(f"""
            color: {TEXT_PRIMARY};
            font-size: 15px;
            font-weight: 700;
            font-family: {FONT_TITLE};
            letter-spacing: -0.3px;
        """)

        self.status_label = QLabel("● connecting…")
        self.status_label.setStyleSheet(f"""
            color: {TEXT_MUTED};
            font-size: 11px;
            font-family: {FONT_SANS};
        """)

        top_bar.addWidget(brand)
        top_bar.addStretch()
        top_bar.addWidget(self.status_label)
        root.addLayout(top_bar)

        # Thin separator
        root.addWidget(Divider())

        # ── Grid of panels ───────────────────────────────────
        grid = QGridLayout()
        grid.setSpacing(12)

        self.panels: dict[str, InsightPanel] = {}
        self.panel_titles: dict[str, str] = {}

        for index, (port, label) in enumerate(streams):
            panel_title = f"{label}  ·  :{port}"
            panel = InsightPanel(panel_title)
            panel.setMinimumSize(300, 480)
            row = index // 2
            col = index % 2
            grid.addWidget(panel, row, col)
            self.panels[str(port)] = panel
            self.panel_titles[str(port)] = panel_title

        root.addLayout(grid, 1)

        # Compute window size
        cols = min(len(streams), 2)
        rows = (len(streams) + 1) // 2
        self.resize(cols * 340 + (cols - 1) * 12 + 32, rows * 500 + rows * 12 + 90)

    # ── Positioning ──────────────────────────────────────────
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

    # ── Slots ─────────────────────────────────────────────────
    def update_panel_raw(self, port_label: str, payload: str):
        panel = self.panels.get(port_label)
        if panel is not None:
            panel.update_raw(payload)

    def update_panel_parsed(self, port_label: str, parsed: dict):
        panel = self.panels.get(port_label)
        if panel is not None:
            panel.update_parsed(parsed)

    def handle_summary(self, port_label: str, summary: dict):
        panel = self.panels.get(port_label)
        if panel is not None:
            panel.update_summary(summary)

        client = self.firebase_clients.get(port_label)
        if client:
            client.push_summary(summary)
            logger.info(f"[Dashboard] Summary pushed to Firebase for port {port_label}")

    def set_connection_status(self, status: str):
        dot_color = {
            "connected":    ACCENT_GREEN,
            "disconnected": ACCENT_RED,
        }.get(status.split()[0].rstrip("—"), ACCENT_AMBER)

        self.status_label.setText(f"● {status}")
        self.status_label.setStyleSheet(f"""
            color: {dot_color};
            font-size: 11px;
            font-family: {FONT_SANS};
        """)


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="MQTT dashboard for stream labels")
    parser.add_argument("--broker")
    parser.add_argument("--broker-port",   type=int)
    parser.add_argument("--raw-topic")
    parser.add_argument("--summary-topic")
    parser.add_argument("--streams",       nargs="+")
    return parser.parse_args()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    logging.basicConfig(level=logging.INFO)
    args = parse_args()

    try:
        streams = parse_streams(args.streams)
    except ValueError as error:
        print(f"Error: {error}", file=sys.stderr)
        sys.exit(2)

    known_labels = {str(port) for port, _ in streams}

    firebase_clients: dict[str, FirebaseClient] = {}
    for port, label in streams:
        firebase_clients[str(port)] = FirebaseClient(computer_id=label)

    app = QApplication(sys.argv)
    app.setStyle("Fusion")

    window = Dashboard(streams, firebase_clients)

    signals = MqttSignals()
    signals.message_received.connect(window.update_panel_raw)
    signals.parsed_received.connect(window.update_panel_parsed)
    signals.summary_received.connect(window.handle_summary)
    signals.connection_status.connect(window.set_connection_status)

    # ── MQTT ─────────────────────────────────────────────────
    mqtt_client = mqtt.Client()
    mqtt_client.reconnect_delay_set(
        min_delay=RECONNECT_DELAY_MIN,
        max_delay=RECONNECT_DELAY_MAX,
    )

    def on_connect(client, userdata, flags, rc):
        if rc == 0:
            logger.info("[MQTT] Connected to broker")
            signals.connection_status.emit("connected")
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
            logger.warning(f"[MQTT] Unexpected disconnect (rc={rc}), retrying…")
            signals.connection_status.emit(f"reconnecting… (rc={rc})")

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

            # Try structured parse first; fall back to raw JSON display
            parsed = parse_mqtt_payload(data)
            if parsed is not None:
                signals.parsed_received.emit(label, parsed)
            else:
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