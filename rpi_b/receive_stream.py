import argparse
import ctypes
import shutil
import subprocess
import sys
import time
from ctypes import wintypes

from stream_config import default_stream_args, parse_streams


def position_windows_streams(processes: list[subprocess.Popen]) -> None:
    if not processes:
        return

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    gw_owner = 4
    sw_restore = 9
    enum_windows_proc = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)

    def find_window_for_pid(pid: int, timeout_seconds: float = 8.0) -> int | None:
        deadline = time.time() + timeout_seconds
        while time.time() < deadline:
            hwnds: list[int] = []

            @enum_windows_proc
            def callback(hwnd, l_param):
                process_id = wintypes.DWORD(0)
                user32.GetWindowThreadProcessId(hwnd, ctypes.byref(process_id))
                if process_id.value != pid:
                    return True
                if not user32.IsWindowVisible(hwnd):
                    return True
                if user32.GetWindow(hwnd, gw_owner):
                    return True
                if user32.GetWindowTextLengthW(hwnd) <= 0:
                    return True
                hwnds.append(hwnd)
                return True

            user32.EnumWindows(callback, 0)
            if hwnds:
                return hwnds[0]
            time.sleep(0.2)

        return None

    screen_width = user32.GetSystemMetrics(0)
    screen_height = user32.GetSystemMetrics(1)

    margin = 20
    top = 20
    bottom_reserved = 460
    usable_height = max(280, screen_height - top - bottom_reserved - margin)

    if len(processes) == 1:
        width = max(640, screen_width - (2 * margin))
        placements = [(margin, top, width, usable_height)]
    else:
        pane_width = max(480, (screen_width - (3 * margin)) // 2)
        placements = [
            (margin, top, pane_width, usable_height),
            (2 * margin + pane_width, top, pane_width, usable_height),
        ]

    for index, process in enumerate(processes[: len(placements)]):
        hwnd = find_window_for_pid(process.pid)
        if hwnd is None:
            print(f"Warning: Unable to find window for stream process {process.pid}")
            continue

        x, y, width, height = placements[index]
        user32.ShowWindow(hwnd, sw_restore)
        moved = user32.MoveWindow(hwnd, x, y, width, height, True)
        if not moved:
            print(f"Warning: Unable to move window for process {process.pid}")


def position_linux_streams(processes: list[subprocess.Popen]) -> None:
    if not processes:
        return

    if not shutil.which("wmctrl"):
        print("Warning: wmctrl not found; cannot position Linux windows automatically.")
        print("Install it with: sudo apt install wmctrl")
        return

    def get_screen_size() -> tuple[int, int]:
        try:
            out = subprocess.check_output(["xrandr", "--current"], text=True, stderr=subprocess.DEVNULL)
            for line in out.splitlines():
                if " current " in line:
                    # Screen 0: minimum 320 x 200, current 1920 x 1080, maximum 16384 x 16384
                    parts = line.split(" current ", 1)[1].split(",", 1)[0]
                    width_str, height_str = parts.split(" x ")
                    return int(width_str.strip()), int(height_str.strip())
        except Exception:
            pass
        return 1920, 1080

    def find_window_id_for_pid(pid: int, timeout_seconds: float = 8.0) -> str | None:
        deadline = time.time() + timeout_seconds
        while time.time() < deadline:
            try:
                out = subprocess.check_output(["wmctrl", "-lp"], text=True, stderr=subprocess.DEVNULL)
                for line in out.splitlines():
                    parts = line.split(None, 4)
                    if len(parts) >= 4:
                        win_id = parts[0]
                        win_pid = parts[2]
                        if win_pid.isdigit() and int(win_pid) == pid:
                            return win_id
            except Exception:
                pass
            time.sleep(0.2)
        return None

    screen_width, screen_height = get_screen_size()

    margin = 20
    top = 20
    bottom_reserved = 460
    usable_height = max(280, screen_height - top - bottom_reserved - margin)

    if len(processes) == 1:
        width = max(640, screen_width - (2 * margin))
        placements = [(margin, top, width, usable_height)]
    else:
        pane_width = max(480, (screen_width - (3 * margin)) // 2)
        placements = [
            (margin, top, pane_width, usable_height),
            (2 * margin + pane_width, top, pane_width, usable_height),
        ]

    for index, process in enumerate(processes[: len(placements)]):
        win_id = find_window_id_for_pid(process.pid)
        if win_id is None:
            print(f"Warning: Unable to find Linux window for stream process {process.pid}")
            continue

        x, y, width, height = placements[index]

        try:
            # Remove maximized state first, otherwise some WMs ignore resize/move
            subprocess.run(
                ["wmctrl", "-ir", win_id, "-b", "remove,maximized_vert,maximized_horz"],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )

            # Move and resize: gravity,x,y,width,height
            subprocess.run(
                ["wmctrl", "-ir", win_id, "-e", f"0,{x},{y},{width},{height}"],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception as e:
            print(f"Warning: Unable to move Linux window for process {process.pid}: {e}")


def get_gst_path(platform_name: str, custom_path: str | None) -> str:
    if custom_path:
        return custom_path

    if platform_name == "windows":
        gst = shutil.which("gst-launch-1.0.exe") or shutil.which("gst-launch-1.0")
    else:
        gst = shutil.which("gst-launch-1.0")

    if not gst:
        print("Error: gst-launch-1.0 was not found.", file=sys.stderr)
        print("Pass it explicitly with --gst-path", file=sys.stderr)
        sys.exit(1)

    return gst


def build_command(gst_path: str, port: int, latency: int, sink: str, label: str) -> list[str]:
    return [
        gst_path,
        "-v",
        "udpsrc",
        f"port={port}",
        "caps=application/x-rtp,media=video,encoding-name=H264,clock-rate=90000,payload=96",
        "!",
        "rtpjitterbuffer",
        f"latency={latency}",
        "!",
        "rtph264depay",
        "!",
        "h264parse",
        "!",
        "avdec_h264",
        "!",
        "videoconvert",
        "!",
        "textoverlay",
        f"text={label}",
        "valignment=top",
        "halignment=left",
        "font-desc=Sans, 24",
        "shaded-background=true",
        "!",
        sink,
        "sync=false",
    ]


def main():
    parser = argparse.ArgumentParser(description="RTP H.264 receiver with static overlay")
    parser.add_argument("platform", choices=["windows", "linux"])
    parser.add_argument("--gst-path", default=None)
    parser.add_argument("--streams", nargs="+", default=default_stream_args())
    parser.add_argument("--latency", type=int, default=50)
    parser.add_argument("--sink", default="autovideosink")
    args = parser.parse_args()

    gst_path = get_gst_path(args.platform, args.gst_path)
    try:
        streams = parse_streams(args.streams)
    except ValueError as error:
        print(f"Error: {error}", file=sys.stderr)
        sys.exit(2)

    print(f"Using GStreamer: {gst_path}")
    print(f"Listening on streams: {streams}")
    print("Press Ctrl+C to stop.")

    processes = []

    try:
        for port, label in streams:
            cmd = build_command(gst_path, port, args.latency, args.sink, label)
            print(f"Starting receiver for port {port} with label '{label}'")
            p = subprocess.Popen(cmd)
            processes.append(p)

        if args.platform == "windows":
            position_windows_streams(processes)
        elif args.platform == "linux":
            position_linux_streams(processes)

        for p in processes:
            p.wait()

    except KeyboardInterrupt:
        print("\nStopping receivers...")
        for p in processes:
            p.terminate()
        for p in processes:
            p.wait()


if __name__ == "__main__":
    main()