import time
import subprocess
import sys 

from VideoStreamClient import VideoStreamClient
from MQTTClient import MQTTClient
from ProcessSupervisor import ProcessSupervisor


def main():
    # =========================
    # CONFIG
    # =========================
    RECEIVER_IP = sys.argv[1]
    LABEL = int(sys.argv[2])

    # =========================
    # INIT COMPONENTS
    # =========================
    video_client = VideoStreamClient(
        host=RECEIVER_IP,
        port=LABEL
    )

    mqtt_client = MQTTClient(
        broker_ip=RECEIVER_IP,
        label=LABEL
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