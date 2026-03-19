import time
import subprocess
import sys 

from video_stream import VideoStreamClient
from mqtt_client import MQTTClient, MQTTConstants
from process_supervisor import ProcessSupervisor


def main():
    # =========================
    # CONFIG
    # =========================
    RECEIVER_IP = sys.argv[1]
    RECEIVER_PORT = int(sys.argv[2])
    LABEL = str(RECEIVER_PORT)

    # =========================
    # INIT COMPONENTS
    # =========================
    video_client = VideoStreamClient(
        host=RECEIVER_IP,
        port=RECEIVER_PORT
    )

    mqtt_client = MQTTClient(
        broker_ip=RECEIVER_IP
    )

    # =========================
    # SUPERVISORS
    # =========================
    video_supervisor = ProcessSupervisor(
        name="videoStreamProcess",
        start_func=video_client.start_video_stream,
        restart_delay=2
    )

    print("Local transmission started. Press Ctrl+C to stop.")

    # =========================
    # MAIN LOOP
    # =========================
    try:
        while True:
            # Keep video stream alive
            video_supervisor.ensure_running()

            # [Other components' logic]

            # MQTT sending
            payload = mqtt_client.build_payload(LABEL)
            mqtt_client.publish(payload)

            time.sleep(1)

    except KeyboardInterrupt:
        print("\nShutting down...")

        video_supervisor.stop()


if __name__ == "__main__":
    main()