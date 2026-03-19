import argparse
import shutil
import subprocess
import sys

class VideoStreamClient():
    """
    This class is responsible for sending the video stream from the Linux machine to the receiver.
    It uses GStreamer to capture the screen, encode it, and send it over RTP/UDP.
    """
    def __init__(self, host: str, port: int, gst_path = None, width=1280, height=720, fps=30, bitrate=2000):
        self.host = host
        self.port = port
        self.width = width
        self.height = height
        self.fps = fps
        self.bitrate = bitrate
        self.gst_path = gst_path or shutil.which("gst-launch-1.0")
        if not self.gst_path:
            print("Error: gst-launch-1.0 was not found in PATH.", file=sys.stderr)
            print("Pass it explicitly with --gst-path", file=sys.stderr)
    
    def build_command(self) -> list[str]:
        caps = f"video/x-raw,format=I420,width={self.width},height={self.height},framerate={self.fps}/1"

        return [
            self.gst_path,
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
            f"bitrate={self.bitrate}",
            f"key-int-max={self.fps}",
            "!",
            "h264parse",
            "!",
            "rtph264pay",
            "config-interval=1",
            "pt=96",
            "!",
            "udpsink",
            f"host={self.host}",
            f"port={self.port}",
            "sync=false",
        ]

    def start_video_stream(self):
        command = self.build_command()
        return subprocess.Popen(command)

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
        help="Receiver UDP port",
    )
    args = parser.parse_args()


    client = VideoStreamClient(
        host=args.host,
        port=args.port
    )

    print("Starting Linux screen stream sender...")
    print("Press Ctrl+C to stop.")

    videoStreamingProcess = subprocess.Popen(client.build_command())

    try:
        videoStreamingProcess.wait()
    except KeyboardInterrupt:
        print("\nStopping sender...")
        videoStreamingProcess.terminate()
        videoStreamingProcess.wait()


# if __name__ == "__main__":
#     main()
