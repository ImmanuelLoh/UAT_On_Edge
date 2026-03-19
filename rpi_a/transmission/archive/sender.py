# Example usage
# python communications/sender.py --host [IP_ADDRESS] --port 5000

import argparse
import shutil
import subprocess
import sys


def get_gst_path(custom_path: str | None) -> str:
    if custom_path:
        return custom_path

    gst = shutil.which("gst-launch-1.0")
    if not gst:
        print("Error: gst-launch-1.0 was not found in PATH.", file=sys.stderr)
        print("Pass it explicitly with --gst-path", file=sys.stderr)
        sys.exit(1)

    return gst


def build_command(
    gst_path: str,
    host: str,
    port: int,
    width: int,
    height: int,
    fps: int,
    bitrate: int,
) -> list[str]:
    caps = f"video/x-raw,format=I420,width={width},height={height},framerate={fps}/1"

    return [
        gst_path,
        "-v",
        "ximagesrc",
        "use-damage=0",
        "show-pointer=true",
        "!",
        "videoconvert",
        "!",
        "videoscale",
        "!",
        "videorate",
        "!",
        caps,
        "!",
        "queue",
        "!",
        "x264enc",
        "tune=zerolatency",
        "speed-preset=ultrafast",
        f"bitrate={bitrate}",
        f"key-int-max={fps}",
        "!",
        "h264parse",
        "!",
        "rtph264pay",
        "config-interval=1",
        "pt=96",
        "!",
        "udpsink",
        f"host={host}",
        f"port={port}",
        "sync=false",
    ]


def main():
    parser = argparse.ArgumentParser(
        description="Linux GStreamer screen sender over RTP/UDP"
    )
    parser.add_argument(
        "--host",
        required=True,
        help="Receiver IP address",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=5000,
        help="Receiver UDP port",
    )
    parser.add_argument(
        "--width",
        type=int,
        default=1280,
        help="Video width",
    )
    parser.add_argument(
        "--height",
        type=int,
        default=720,
        help="Video height",
    )
    parser.add_argument(
        "--fps",
        type=int,
        default=30,
        help="Frame rate",
    )
    parser.add_argument(
        "--bitrate",
        type=int,
        default=2000,
        help="x264 bitrate in kbps",
    )
    parser.add_argument(
        "--gst-path",
        default=None,
        help="Full path to gst-launch-1.0",
    )

    args = parser.parse_args()

    gst_path = get_gst_path(args.gst_path)

    command = build_command(
        gst_path=gst_path,
        host=args.host,
        port=args.port,
        width=args.width,
        height=args.height,
        fps=args.fps,
        bitrate=args.bitrate,
    )

    print("Starting Linux screen stream sender...")
    print(f"GStreamer: {gst_path}")
    print(f"Streaming to: {args.host}:{args.port}")
    print(f"Format: {args.width}x{args.height} @ {args.fps} fps")
    print(f"Bitrate: {args.bitrate} kbps")
    print("Press Ctrl+C to stop.")

    process = subprocess.Popen(command)

    try:
        process.wait()
    except KeyboardInterrupt:
        print("\nStopping sender...")
        process.terminate()
        process.wait()


if __name__ == "__main__":
    main()
