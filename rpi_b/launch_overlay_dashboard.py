# Need to start MQTT broker
# On Linux
#   sudo systemctl enable mosquitto
#   sudo systemctl start mosquitto
# On Windows, can use
#   cd "C:\Program Files\Mosquitto"
#   mosquitto.exe -c mosquitto.conf -v

import argparse
import subprocess
import sys
from pathlib import Path

from stream_config import default_stream_args, parse_streams


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Launch static overlay receiver + MQTT dashboard")
    parser.add_argument("platform", choices=["windows", "linux"])
    parser.add_argument("--streams", nargs="+", default=default_stream_args())
    parser.add_argument("--gst-path", default=None)
    parser.add_argument("--latency", type=int, default=50)
    parser.add_argument("--sink", default="autovideosink")
    parser.add_argument("--broker", default="127.0.0.1")
    parser.add_argument("--broker-port", type=int, default=1883)
    parser.add_argument("--raw-topic", default="uat/raw")
    return parser.parse_args()


def main():
    args = parse_args()
    try:
        streams = parse_streams(args.streams)
    except ValueError as error:
        print(f"Error: {error}", file=sys.stderr)
        sys.exit(2)
    stream_args = [f"{port}={label}" for port, label in streams]

    base_dir = Path(__file__).resolve().parent
    overlay_script = base_dir / "receive_stream.py"
    dashboard_script = base_dir / "mqtt_dashboard.py"

    overlay_cmd = [
        sys.executable,
        str(overlay_script),
        args.platform,
        "--streams",
        *stream_args,
        "--latency",
        str(args.latency),
        "--sink",
        args.sink,
    ]

    if args.gst_path:
        overlay_cmd.extend(["--gst-path", args.gst_path])

    dashboard_cmd = [
        sys.executable,
        str(dashboard_script),
        "--broker",
        args.broker,
        "--broker-port",
        str(args.broker_port),
        "--raw-topic",
        args.raw_topic,
        "--streams",
        *stream_args,
    ]

    print(f"Starting with streams: {streams}")
    print(f"Overlay command: {' '.join(overlay_cmd)}")
    print(f"Dashboard command: {' '.join(dashboard_cmd)}")
    print("Press Ctrl+C to stop both processes.")

    processes: list[subprocess.Popen] = []

    try:
        processes.append(subprocess.Popen(overlay_cmd, cwd=base_dir))
        processes.append(subprocess.Popen(dashboard_cmd, cwd=base_dir))

        for process in processes:
            process.wait()
    except KeyboardInterrupt:
        print("\nStopping processes...")
    finally:
        for process in processes:
            if process.poll() is None:
                process.terminate()
        for process in processes:
            if process.poll() is None:
                process.wait()


if __name__ == "__main__":
    main()
