# UAT on Edge

An edge-based User Acceptance Testing (UAT) assistant that monitors participants in real time and delivers contextual hints when they appear to be struggling — using computer vision, mouse tracking, and an on-device LLM.

---

## Architecture Overview

```
┌──────────────────────────────────┐
│         Tester's Browser          │
│   (opens http://<rpi-a>:5000)    │
└────────────────┬─────────────────┘
                 │ HTTP
                 ▼
┌──────────────────────────────────────────────────────┐
│                  RPi A  (Testing Machine)              │
│                                                        │
│  Flask app (5000)  ←→  Trigger Engine                  │
│  4-page UAT task flow      10+ signal composite score  │
│  Chat UI                                               │
│                                                        │
│  Sensors                   Context Buffer (5 s window) │
│  ├─ Face (MediaPipe)       └─ Summarises events for    │
│  │   emotion, attention,       LLM prompt              │
│  │   gaze                                              │
│  ├─ Mouse (pynput)                                     │
│  │   idle time, click rate                             │
│  └─ Web tracker (Selenium)                             │
│      task page / form state                            │
│                                                        │
│  MQTT publisher  ─────────────────────────────────┐   │
│  (uat/raw, uat/summary, uat/replay)                │   │
└─────────────────────┬──────────────────────────────┘   │
                      │ HTTP :5001                        │
                      ▼                                   │ MQTT
         ┌────────────────────────┐                       │ :1883
         │   Laptop  LLM Server   │                       │
         │  Flask API  +  Ollama  │                       │
         │  Llama 3.2 (3 B)       │                       │
         └────────────────────────┘                       │
                                                          ▼
                                      ┌───────────────────────────────┐
                                      │    RPi B  (Supervisor Machine) │
                                      │                                │
                                      │  Qt dashboard (real-time view) │
                                      │  Firebase client (60 s chunks) │
                                      └───────────────┬────────────────┘
                                                      │ HTTPS
                                                      ▼
                                             Firebase Firestore
```

### Component responsibilities

| Node | Handles |
|------|---------|
| **RPi A** | UAT web UI, all sensor capture, trigger scoring, context buffering, LLM request/response, MQTT publishing |
| **Laptop** | LLM inference (Ollama/Llama 3.2), prompt construction, response generation |
| **RPi B** | Real-time supervisor dashboard, Firebase upload, session archival & replay |

---

## File Structure

```
UAT_On_Edge/
├── requirements.txt
│
├── rpi_a/
│   ├── app.py                  # Flask entry point, route handlers
│   ├── config.py               # Thresholds, LLM URL, cooldown timings
│   ├── trigger_engine.py       # Composite frustration score (10+ signals)
│   ├── context_buffer.py       # 5-second sliding event window
│   ├── llm_client.py           # HTTP client → Laptop LLM, 5 s fallback
│   ├── tracker_bridge.py       # Sensor orchestrator, MQTT publisher, session recorder
│   │
│   ├── sensors/
│   │   ├── main.py             # Face sensor process entry point
│   │   ├── face_sensor.py      # Unified face pipeline (baseline → gaze calibration → analytics)
│   │   ├── mouse_tracker.py    # Idle time, click rate, quadrant tracking
│   │   ├── web_tracker.py      # Selenium JS listener for form events
│   │   ├── uat_monitor.py      # Task transition & form validation detection
│   │   └── face/
│   │       ├── HeadPose.py
│   │       ├── EyeAnalytics.py
│   │       ├── FaceAnalytics.py
│   │       └── GazeCalibrator.py
│   │
│   ├── transmission/
│   │   ├── MQTTClient.py       # Paho MQTT wrapper
│   │   ├── VideoStreamClient.py
│   │   └── ProcessSupervisor.py
│   │
│   ├── templates/
│   │   ├── page1.html          # Welcome / instructions
│   │   ├── page2.html          # Task: click the colour
│   │   ├── page3.html          # Task: number selection
│   │   └── page4.html          # Completion page
│   └── static/
│       ├── css/style.css
│       └── js/chat.js          # Chat overlay, sends events to Flask API
│
├── rpi_b/
│   ├── mqtt_dashboard.py       # Entry point — MQTT listener + Qt app bootstrap
│   ├── dashboard_ui.py         # Qt5 real-time dashboard
│   ├── firebase_client.py      # Firestore uploader (chunk-based)
│   ├── payload_parsers.py      # MQTT JSON → structured data
│   ├── stream_config.py        # CLI arg parser for port=label streams
│   └── receive_stream.py       # Multi-window positioning (Windows)
│
├── laptop_llm/
│   ├── llm_server.py           # Flask API :5001, builds prompt, calls Ollama
│   └── launcher.py             # Starts Ollama + llm_server via PowerShell
│
└── cloud_dashboard/
    ├── server.js               # Node.js dashboard server
    └── lab_dashboard.html      # Supervisor web UI (live + historical view)
```

---

## Setup

### 1. Install Python 3.11.9 on Raspberry Pi OS 13 (Trixie)

**Build dependencies:**
```bash
sudo apt update
sudo apt install -y \
  build-essential libssl-dev zlib1g-dev libncurses5-dev \
  libffi-dev libsqlite3-dev libreadline-dev libbz2-dev \
  liblzma-dev tk-dev wget curl
```

**Compile Python:**
```bash
cd ~
wget https://www.python.org/ftp/python/3.11.9/Python-3.11.9.tgz
tar -xzf Python-3.11.9.tgz && cd Python-3.11.9
./configure --enable-optimizations --prefix=/usr/local/python3.11
make -j$(nproc)
sudo make altinstall
```

**Verify tkinter:**
```bash
/usr/local/python3.11/bin/python3.11 -c "import tkinter; print('Tk OK')"
```

### 2. Install system dependencies

#### RPi A

**Chromium + chromedriver** — required by the Selenium web tracker (`web_tracker.py` hardcodes `/usr/bin/chromium` and `/usr/bin/chromedriver`):
```bash
sudo apt install -y chromium chromium-driver
```

**GStreamer** — required for screen capture and RTP/UDP video streaming to RPi B. The pipeline uses `ximagesrc → x264enc → rtph264pay → udpsink`:
```bash
sudo apt install -y \
  gstreamer1.0-tools \
  gstreamer1.0-plugins-base \
  gstreamer1.0-plugins-good \
  gstreamer1.0-plugins-bad \
  gstreamer1.0-plugins-ugly \
  gstreamer1.0-x
```

Verify:
```bash
gst-launch-1.0 --version
```

**X11** — `ximagesrc` (GStreamer screen capture) and Chromium both require an active X11 display. RPi OS Trixie defaults to Wayland; switch to X11 or ensure `DISPLAY` is set:

```bash
# Option A: force X11 session at login (raspi-config → Advanced → Wayland → X11)
# Option B: if already in an X session, just confirm DISPLAY is set
echo $DISPLAY          # should print :0 or similar

# Option C: headless / SSH — run a virtual framebuffer
sudo apt install -y xvfb
Xvfb :99 -screen 0 1280x720x24 &
export DISPLAY=:99
```

> Note: `export DISPLAY=:0` (or `:99` for Xvfb) must be set in the shell that launches `app.py` so that both GStreamer and Chromium can find the display.

**VNC grey screen fix** — when the RPi boots without a monitor plugged in, the HDMI output is often marked `disconnected` by xrandr. VNC connects to the X session but finds no active framebuffer, rendering a grey screen. `ximagesrc` and Chromium both fail silently in this state. Fix it with:
```bash
xrandr --query                      # check which output is available (e.g. HDMI-1)
xrandr --output HDMI-1 --auto       # force it on at preferred resolution
```

If the display still doesn't come up, restart the display manager:
```bash
sudo systemctl restart lightdm
```

**v4l2-utils** — `CameraStream.py` calls `v4l2-ctl` directly to configure `/dev/video0` (exposure, gain, brightness):
```bash
sudo apt install -y v4l2-utils
```

**OpenCV headless fix** — `opencv-python` requires `libGL.so.1` at import time. On a desktop session this is already present; on a headless/SSH session install:
```bash
sudo apt install -y libgl1
```

**PortAudio** — required by `sounddevice` (listed in `requirements.txt`):
```bash
sudo apt install -y libportaudio2
```

**Camera and input group access** — the face sensor opens `/dev/video0` and `evdev` reads `/dev/input/event*`. Add your user to both groups, then log out and back in:
```bash
sudo usermod -aG video,input $USER
```

#### RPi B

**PySide6** — the Qt dashboard (`dashboard_ui.py`, `mqtt_dashboard.py`) uses PySide6, which is not in `requirements.txt`:
```bash
pip install PySide6
```

**firebase-admin** — also not in `requirements.txt`, required by `firebase_client.py`:
```bash
pip install firebase-admin
```

Place your Firebase service account key at `rpi_b/serviceAccountKey.json` (Firebase Console → Project Settings → Service Accounts → Generate new private key).

#### MQTT broker (any machine on the network)

All nodes communicate via MQTT. If you don't have a broker already, install Mosquitto on one machine (e.g. RPi B):
```bash
sudo apt install -y mosquitto mosquitto-clients
sudo systemctl enable --now mosquitto
```

Verify it's reachable from RPi A:
```bash
mosquitto_sub -h <BROKER_IP> -t test
```

### 3. Create virtualenv and install Python dependencies

```bash
cd UAT_On_Edge
/usr/local/python3.11/bin/python3.11 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

---

## Running

### RPi A — Testing machine
```bash
cd rpi_a
python app.py          # Flask UI at http://0.0.0.0:5000
# tracker_bridge.py auto-starts sensors as subprocesses
```

### Laptop — LLM server
```bash
cd laptop_llm
python launcher.py     # Starts Ollama + Flask API at :5001
```

Or manually (requires Ollama already running on `localhost:11434`):
```bash
python llm_server.py
```

### RPi B — Supervisor dashboard
```bash
cd rpi_b
python mqtt_dashboard.py \
  --broker <BROKER_IP> \
  --broker-port 1883 \
  --streams 5000=Computer1 5002=Computer2
```

---

## Configuration

Key tunables are in `rpi_a/config.py`:

| Setting | Default | Description |
|---------|---------|-------------|
| `LLM_URL` | `http://<laptop>:5001/assist` | LLM server endpoint |
| `TRIGGER_THRESHOLD` | `0.7` | Score above which an LLM hint is sent |
| `NUDGE_THRESHOLD` | `0.5` | Score for a lighter nudge |
| `COOLDOWN_SECONDS` | `20` | Minimum gap between hints |
| `CONTEXT_WINDOW_SECONDS` | `5` | Sliding window for event aggregation |

Firebase credentials go in `rpi_b/serviceAccountKey.json` (excluded from version control via `.gitignore`).

---

## MQTT Topics

| Topic | Publisher | Content |
|-------|-----------|---------|
| `uat/raw` | RPi A | Per-tick sensor snapshot (~1 s cadence) |
| `uat/summary` | RPi A | End-of-session summary |
| `uat/replay` | RPi A | Chunked replay fragments |
