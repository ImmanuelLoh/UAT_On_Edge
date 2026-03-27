"""
mqtt_dashboard.py
Entry point — MQTT broker connection, Firebase routing, and Qt app bootstrap.

Imports all UI from dashboard_ui.py.
Imports all payload parsing from payload_parsers.py.
"""

import argparse
import json
import logging
import sys

import paho.mqtt.client as mqtt
from PySide6.QtWidgets import QApplication

from stream_config import parse_streams
from firebase_client import FirebaseClient
from payload_parsers import parse_mqtt_payload, parse_summary_payload
from dashboard_ui import Dashboard, MqttSignals

logger = logging.getLogger(__name__)

RECONNECT_DELAY_MIN = 1
RECONNECT_DELAY_MAX = 30


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="MQTT dashboard for stream labels")
    parser.add_argument("--broker")
    parser.add_argument("--broker-port",   type=int)
    parser.add_argument("--raw-topic")
    parser.add_argument("--summary-topic")
    parser.add_argument("--replay-topic")
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

    # One FirebaseClient per stream
    firebase_clients: dict[str, FirebaseClient] = {
        str(port): FirebaseClient(computer_id=label)
        for port, label in streams
    }

    app = QApplication(sys.argv)
    app.setStyle("Fusion")

    window  = Dashboard(streams, firebase_clients)
    signals = MqttSignals()

    signals.message_received.connect(window.update_panel_raw)
    signals.parsed_received.connect(window.update_panel_parsed)
    signals.summary_received.connect(window.handle_summary)
    signals.connection_status.connect(window.set_connection_status)

    # ── MQTT callbacks ────────────────────────────────────────

    def on_connect(client, userdata, flags, rc):
        if rc == 0:
            logger.info("[MQTT] Connected to broker")
            signals.connection_status.emit("connected")
            client.subscribe(args.raw_topic)
            client.subscribe(args.summary_topic)
            client.subscribe(args.replay_topic)
            logger.info(f"[MQTT] Subscribed to {args.raw_topic}, "
                        f"{args.summary_topic}, {args.replay_topic}")
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
        import json
        import logging
        from payload_parsers import parse_mqtt_payload, parse_summary_payload
    
        logger = logging.getLogger(__name__)
        raw_payload = msg.payload.decode("utf-8", errors="replace") 
        try:
            data = json.loads(raw_payload)
        except json.JSONDecodeError:
            logger.warning(f"[MQTT] Invalid JSON on {msg.topic}: {raw_payload[:80]}")
            return
    
        # ── uat/replay ────────────────────────────────────────────────────────
        if msg.topic == args.replay_topic:
            label = str(data.get("label", "")).strip()
            if label not in known_labels:
                logger.warning(f"[MQTT] Replay: unknown label '{label}'")
                return
    
            session_id = data.get("session_id")
            seq        = data.get("seq")
            total      = data.get("total")
            ticks      = data.get("ticks", [])
    
            if session_id is None or seq is None or total is None:
                logger.warning(f"[MQTT] Replay fragment missing fields — skipping")
                return
    
            logger.info(f"[MQTT] Replay fragment {seq + 1}/{total} for "
                        f"label={label} session={session_id} ({len(ticks)} ticks)")
    
            client_fb = firebase_clients.get(label)
            if client_fb:
                client_fb.ingest_replay_fragment(
                    session_id=session_id,
                    seq=seq,
                    total=total,
                    ticks=ticks,
                )
            return  # nothing to display on the dashboard for replay fragments
    
        # ── check for raw / summary ────────────────────────────
        label = str(data.get("label") or data.get("meta", {}).get("label", "")).strip()
        if label not in known_labels:
            logger.warning(f"[MQTT] Unknown label '{label}' in payload")
            return
    
        # ── uat/summary ───────────────────────────────────────────────────────
        if msg.topic == args.summary_topic:
            print(f"[MQTT] Summary received for label '{label}'")
            if parse_summary_payload(data) is None:
                logger.warning(f"[MQTT] Malformed summary from label '{label}'")
    
            signals.summary_received.emit(label, data)
    
            client_fb = firebase_clients.get(label)
            if client_fb:
                try:
                    client_fb.push_summary(data)
                    logger.info(f"[Firebase] Summary uploaded for label {label}")
                except Exception as e:
                    logger.warning(f"[Firebase] push_summary failed for label {label}: {e}")
    
        # ── uat/raw (regular analytics tick) ─────────────────────────────────
        else:
            client_fb = firebase_clients.get(label)
            if client_fb:
                client_fb.push(data)
    
            parsed = parse_mqtt_payload(data)
            if parsed is not None:
                signals.parsed_received.emit(label, parsed)
            else:
                signals.message_received.emit(label, json.dumps(data, indent=2))

    # ── MQTT client setup ─────────────────────────────────────

    mqtt_client = mqtt.Client()
    mqtt_client.reconnect_delay_set(
        min_delay=RECONNECT_DELAY_MIN,
        max_delay=RECONNECT_DELAY_MAX,
    )
    mqtt_client.on_connect    = on_connect
    mqtt_client.on_disconnect = on_disconnect
    mqtt_client.on_message    = on_message

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
        for fb_client in firebase_clients.values():
            fb_client.stop()


if __name__ == "__main__":
    main()