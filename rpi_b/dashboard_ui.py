"""
dashboard_ui.py
Qt UI components for the MQTT Insights Dashboard.

Exports:
    Dashboard       — main QWidget window
    InsightPanel    — per-stream card (live + summary views)
    MqttSignals     — Qt signals for cross-thread communication

    Primitives: Divider, SectionHeader, DataRow
    Helpers:    emotion_color, score_color
    Constants:  LIGHT_BG, CARD_BG, BORDER, TEXT_*, ACCENT_*, FONT_*
"""

import logging

from PySide6.QtCore import Signal, QObject, Qt
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from payload_parsers import parse_summary_payload

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Design tokens
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

FONT_MONO  = "JetBrains Mono, Menlo, Consolas, monospace"
FONT_SANS  = "SF Pro Text, Segoe UI, Helvetica Neue, sans-serif"
FONT_TITLE = "SF Pro Display, Segoe UI Semibold, Helvetica Neue, sans-serif"


# ---------------------------------------------------------------------------
# Color helpers
# ---------------------------------------------------------------------------

def emotion_color(emotion: str) -> str:
    mapping = {
        "FRUSTRATED": ACCENT_RED,
        "HAPPY":      ACCENT_GREEN,
        "NEUTRAL":    TEXT_SECONDARY,
        "SAD":        ACCENT_BLUE,
        "ANGRY":      ACCENT_RED,
        "SURPRISED":  ACCENT_AMBER,
    }
    return mapping.get(str(emotion).upper(), TEXT_PRIMARY)


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


# ---------------------------------------------------------------------------
# Primitive widgets
# ---------------------------------------------------------------------------

class Divider(QFrame):
    def __init__(self, vertical: bool = False):
        super().__init__()
        if vertical:
            self.setFrameShape(QFrame.Shape.VLine)
            self.setStyleSheet(f"background: {BORDER}; width: 1px; border: none;")
            self.setFixedWidth(1)
        else:
            self.setFrameShape(QFrame.Shape.HLine)
            self.setStyleSheet(f"background: {BORDER}; height: 1px; border: none;")
            self.setFixedHeight(1)


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
    """A single key → value row with optional value colour."""

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


# ---------------------------------------------------------------------------
# Signals  (lives here so Dashboard and mqtt_dashboard both import one place)
# ---------------------------------------------------------------------------

class MqttSignals(QObject):
    message_received  = Signal(str, str)   # port_label, pretty_payload (fallback)
    parsed_received   = Signal(str, dict)  # port_label, parsed_dict
    summary_received  = Signal(str, dict)  # port_label, raw summary_dict
    connection_status = Signal(str)


# ---------------------------------------------------------------------------
# InsightPanel — per-stream card
# ---------------------------------------------------------------------------

class InsightPanel(QWidget):
    """
    Three-column card:
        col_a  Task & Mouse
        col_b  Face & Gaze
        col_c  LLM Assistant  (wider)

    Supports two display modes:
        update_parsed()  — live tick data
        update_summary() — end-of-session summary card
        update_raw()     — fallback monospace dump
    """

    def __init__(self, title: str):
        super().__init__()
        self._title = title
        self._llm_activated   = False
        self._llm_last_role   = None
        self._llm_last_message = ""
        self._build_ui()

    # ── Build ────────────────────────────────────────────────

    def _build_ui(self):
        self.setObjectName("InsightPanel")
        self.setStyleSheet(f"""
            #InsightPanel {{
                background: {CARD_BG};
                border: 1px solid {BORDER};
                border-radius: 10px;
            }}
        """)

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(16, 12, 16, 12)
        main_layout.setSpacing(10)

        # Header row
        header_row = QHBoxLayout()
        title_lbl = QLabel(self._title)
        title_lbl.setStyleSheet(
            f"color: {TEXT_PRIMARY}; font-size: 14px; font-weight: 700; font-family: {FONT_TITLE};"
        )
        self._dot = QLabel("●")
        self._dot.setStyleSheet(f"color: {TEXT_MUTED}; font-size: 10px;")
        header_row.addWidget(title_lbl)
        header_row.addStretch()
        header_row.addWidget(self._dot)
        main_layout.addLayout(header_row)
        main_layout.addWidget(Divider())

        # Three-column content area
        self.columns_container = QHBoxLayout()
        self.columns_container.setSpacing(15)

        self.col_a = QVBoxLayout()   # Task & Mouse
        self.col_b = QVBoxLayout()   # Face & Gaze
        self.col_c = QVBoxLayout()   # LLM (wider)

        self.columns_container.addLayout(self.col_a, 1)
        self.columns_container.addWidget(Divider(vertical=True))
        self.columns_container.addLayout(self.col_b, 1)
        self.columns_container.addWidget(Divider(vertical=True))
        self.columns_container.addLayout(self.col_c, 2)

        main_layout.addLayout(self.columns_container)

    # ── Helpers ──────────────────────────────────────────────

    def _clear_columns(self):
        for layout in (self.col_a, self.col_b, self.col_c):
            while layout.count():
                item = layout.takeAt(0)
                if item.widget():
                    item.widget().deleteLater()

    def set_status_dot(self, color: str):
        self._dot.setStyleSheet(f"color: {color}; font-size: 10px;")

    # ── Live tick view ────────────────────────────────────────

    def update_parsed(self, parsed: dict):
        # Persist LLM state across ticks
        if parsed.get("llm_activated"):
            self._llm_activated = True

        incoming_msg = parsed.get("llm_last_message", "")
        if incoming_msg:
            self._llm_last_message = incoming_msg
            self._llm_last_role    = parsed.get("llm_last_role")

        if not parsed.get("llm_activated") and not incoming_msg:
            if self._llm_activated:
                self._llm_activated    = False
                self._llm_last_role    = None
                self._llm_last_message = ""

        self._clear_columns()
        self.set_status_dot(ACCENT_GREEN)

        # Column A — Task & Mouse
        self.col_a.addWidget(SectionHeader("Task"))
        self.col_a.addWidget(DataRow("Current", str(parsed["task"])))
        self.col_a.addWidget(DataRow("Correct", str(parsed["correct_click"])))
        self.col_a.addWidget(DataRow("Wrong",   str(parsed["wrong_click"])))

        self.col_a.addSpacing(12)
        self.col_a.addWidget(SectionHeader("Mouse"))
        m_status = str(parsed["mouse_status"]).upper()
        m_color  = ACCENT_GREEN if m_status == "ACTIVE" else ACCENT_AMBER
        self.col_a.addWidget(DataRow("Status",  m_status, m_color))
        self.col_a.addWidget(DataRow("Idle (s)", str(parsed["idle_time"])))
        self.col_a.addStretch()

        # Column B — Face & Gaze
        self.col_b.addWidget(SectionHeader("Biometrics"))
        self.col_b.addWidget(DataRow("Emotion",     str(parsed["emotion"]),           emotion_color(parsed["emotion"])))
        self.col_b.addWidget(DataRow("Frustration", str(parsed["frustration_score"]), score_color(parsed["frustration_score"])))
        self.col_b.addWidget(DataRow("Attention",   str(parsed["attention_score"])))
        self.col_b.addWidget(DataRow("Direction", str(parsed["direction"])))

        self.col_b.addSpacing(12)
        self.col_b.addWidget(SectionHeader("Gaze"))
        self.col_b.addWidget(DataRow("Quadrant", str(parsed["gaze_quadrant"])))

        # Inline frustration alert (right under gaze)
        if parsed.get("frustration_alert"):
            alert_lbl = QLabel("Frustration alert: User has been frustrated for a while!")
            alert_lbl.setStyleSheet(f"""
                color: {ACCENT_RED};
                font-size: 12px;
                font-weight: 600;
                font-family: {FONT_SANS};
                background: {LIGHT_BG};
                border-left: 3px solid {ACCENT_RED};
                border-radius: 4px;
                padding: 6px 8px;
            """)
            alert_lbl.setWordWrap(True)
            self.col_b.addWidget(alert_lbl)

        self.col_b.addStretch()

        # Column C — LLM Assistant
        self.col_c.addWidget(SectionHeader("AI Assistant"))
        status_text, status_color = (
            ("Activated", ACCENT_GREEN) if self._llm_activated else ("Idle", TEXT_MUTED)
        )
        self.col_c.addWidget(DataRow("Status", status_text, status_color))

        if self._llm_last_message:
            role_label = "Assistant" if self._llm_last_role == "assistant" else "User"
            role_color = ACCENT_BLUE  if self._llm_last_role == "assistant" else TEXT_PRIMARY
            self.col_c.addWidget(DataRow("Speaker", role_label, role_color))

            msg_lbl = QLabel(self._llm_last_message)
            msg_lbl.setWordWrap(True)
            msg_lbl.setStyleSheet(f"""
                color: {TEXT_PRIMARY}; font-size: 12px; background: {LIGHT_BG};
                border-left: 3px solid {role_color}; border-radius: 4px; padding: 8px;
            """)
            self.col_c.addWidget(msg_lbl)

        if parsed.get("llm_timeout"):
            alert_lbl = QLabel("LLM has failed, please assist users!")
            alert_lbl.setWordWrap(True)
            alert_lbl.setStyleSheet(f"""
                color: {ACCENT_RED};
                font-size: 12px;
                font-weight: 600;
                font-family: {FONT_SANS};
                background: {LIGHT_BG};
                border-left: 3px solid {ACCENT_RED};
                border-radius: 4px;
                padding: 6px 8px;
            """)
            self.col_c.addWidget(alert_lbl)

        self.col_c.addStretch()

    # ── Raw fallback view ─────────────────────────────────────

    def update_raw(self, raw_json: str):
        self._clear_columns()
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
        self.col_a.addWidget(lbl)

    # ── End-of-session summary view ───────────────────────────

    def update_summary(self, summary: dict):
        parsed = parse_summary_payload(summary)

        self._clear_columns()
        self.set_status_dot(ACCENT_BLUE)

        session_banner = QLabel("SESSION ENDED")
        session_banner.setStyleSheet(f"""
            color: {ACCENT_BLUE};
            font-size: 11px;
            font-weight: 700;
            letter-spacing: 1.5px;
            font-family: {FONT_SANS};
            padding-bottom: 4px;
        """)

        if parsed is None:
            self.col_a.addWidget(session_banner)
            self.col_a.addWidget(QLabel("Could not parse summary."))
            return

        # Column A — Session meta + task performance + mouse
        self.col_a.addWidget(session_banner)
        self.col_a.addSpacing(4)

        self.col_a.addWidget(SectionHeader("Session"))
        self.col_a.addWidget(DataRow("Duration",  str(parsed["duration"])))
        self.col_a.addWidget(DataRow("Snapshots", str(parsed["total_snapshots"])))
        status_color = ACCENT_RED if parsed["session_active"] else ACCENT_GREEN
        self.col_a.addWidget(DataRow("Status", "Inactive", status_color))

        self.col_a.addSpacing(10)
        self.col_a.addWidget(SectionHeader("Task Performance"))
        self.col_a.addWidget(DataRow("Correct Clicks", str(parsed["total_correct_clicks"]), ACCENT_GREEN))
        wrong       = parsed["total_wrong_clicks"]
        wrong_color = ACCENT_RED if isinstance(wrong, int) and wrong > 0 else ACCENT_GREEN
        self.col_a.addWidget(DataRow("Wrong Clicks", str(wrong), wrong_color))

        self.col_a.addSpacing(10)
        self.col_a.addWidget(SectionHeader("Mouse"))
        self.col_a.addWidget(DataRow("Avg Idle (s)",  str(parsed["avg_idle_time"])))
        self.col_a.addWidget(DataRow("Peak Idle (s)", str(parsed["peak_idle_time"])))
        # self.col_a.addWidget(DataRow("Avg CPS",       str(parsed["avg_cps"])))
        # self.col_a.addWidget(DataRow("Top Quadrant",  str(parsed["dominant_quadrant"])))
        self.col_a.addStretch()

        # Column B — Biometrics + gaze
        self.col_b.addWidget(SectionHeader("Biometrics"))
        self.col_b.addWidget(DataRow("Avg Frustration",  str(parsed["avg_frustration"]),  score_color(parsed["avg_frustration"])))
        self.col_b.addWidget(DataRow("Peak Frustration", str(parsed["peak_frustration"]), score_color(parsed["peak_frustration"])))
        self.col_b.addWidget(DataRow("Avg Attention",    str(parsed["avg_attention"])))
        self.col_b.addWidget(DataRow("Avg Blink Rate",   str(parsed["avg_blink_rate"])))

        self.col_b.addSpacing(10)
        self.col_b.addWidget(SectionHeader("Gaze"))
        self.col_b.addWidget(DataRow("Dominant Emotion", str(parsed["dominant_emotion"]), emotion_color(parsed["dominant_emotion"])))
        self.col_b.addWidget(DataRow("Dominant Gaze",    str(parsed["dominant_gaze"])))
        self.col_b.addStretch()

        # Column C — LLM summary
        self.col_c.addWidget(SectionHeader("AI Assistant"))

        activation_by_task = parsed.get("llm", {}).get("activation_by_task", {}) or {}
        act_count = sum(1 for activated in activation_by_task.values() if activated)
        act_color = ACCENT_GREEN if act_count > 0 else TEXT_MUTED

        self.col_c.addWidget(DataRow("Activations", str(act_count), act_color))

        if activation_by_task:
            self.col_c.addSpacing(6)
            self.col_c.addWidget(SectionHeader("Activated Tasks"))
            for task_name, activated in activation_by_task.items():
                status_text = "Activated" if activated else "Not activated"
                status_color = ACCENT_GREEN if activated else TEXT_MUTED
                self.col_c.addWidget(DataRow(task_name, status_text, status_color))

        self.col_c.addStretch()


# ---------------------------------------------------------------------------
# Dashboard window
# ---------------------------------------------------------------------------

class Dashboard(QWidget):
    def __init__(
        self,
        streams: list[tuple[int, str]],
        firebase_clients: dict,
    ):
        super().__init__()
        self.setWindowTitle("MQTT Insights Dashboard")
        self.firebase_clients = firebase_clients

        palette = self.palette()
        palette.setColor(QPalette.ColorRole.Window, QColor(LIGHT_BG))
        self.setPalette(palette)
        self.setAutoFillBackground(True)

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 16)
        root.setSpacing(10)

        # Top bar
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
        self.status_label.setStyleSheet(f"color: {TEXT_MUTED}; font-size: 11px; font-family: {FONT_SANS};")
        top_bar.addWidget(brand)
        top_bar.addStretch()
        top_bar.addWidget(self.status_label)
        root.addLayout(top_bar)
        root.addWidget(Divider())

        # Panel grid
        grid = QGridLayout()
        grid.setSpacing(12)

        self.panels: dict[str, InsightPanel] = {}

        for index, (port, label) in enumerate(streams):
            panel = InsightPanel(f"{label}  ·  :{port}")
            panel.setMinimumSize(850, 280)
            grid.addWidget(panel, index // 2, index % 2)
            self.panels[str(port)] = panel

        root.addLayout(grid, 1)

        cols = min(len(streams), 2)
        rows = (len(streams) + 1) // 2
        self.resize(cols * 340 + (cols - 1) * 12 + 32, rows * 500 + rows * 12 + 90)

    # ── Positioning ──────────────────────────────────────────

    def position_bottom_center(self):
        screen = QApplication.primaryScreen()
        if screen is None:
            return
        available = screen.availableGeometry()
        x = available.x() + (available.width()  - self.width())  // 2
        y = available.y() + available.height() - self.height() - 20
        self.move(max(available.x(), x), max(available.y(), y))

    def showEvent(self, event):
        super().showEvent(event)
        self.position_bottom_center()

    # ── Slots (connected to MqttSignals) ─────────────────────

    def update_panel_raw(self, port_label: str, payload: str):
        panel = self.panels.get(port_label)
        if panel:
            panel.update_raw(payload)

    def update_panel_parsed(self, port_label: str, parsed: dict):
        panel = self.panels.get(port_label)
        if panel:
            panel.update_parsed(parsed)

    def handle_summary(self, port_label: str, summary: dict):
        panel = self.panels.get(port_label)
        if panel:
            panel.update_summary(summary)

    def set_connection_status(self, status: str):
        dot_color = {
            "connected":    ACCENT_GREEN,
            "disconnected": ACCENT_RED,
        }.get(status.split()[0].rstrip("—"), ACCENT_AMBER)

        self.status_label.setText(f"● {status}")
        self.status_label.setStyleSheet(
            f"color: {dot_color}; font-size: 11px; font-family: {FONT_SANS};"
        )