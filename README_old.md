## File Structure
```
project/
│
├── rpi_a/
│   ├── app.py
│   ├── config.py
│   ├── trigger_engine.py
│   ├── context_buffer.py
│   ├── llm_client.py
│   ├── sensors/
│   │   ├── simulated_mouse.py
│   │   ├── simulated_face.py
│   │   └── simulated_task.py
│   ├── templates/
│   │   └── index.html
│   └── static/
│       ├── css/
│       │   └── style.css
│       └── js/
│           └── chat.js
├── rpi_b/
│   ├── app.py
|
└── laptop_llm/
    └── llm_server.py
```
### LLM Breakdown

RPi A Handles:

- screen display for tester

- live sensing

- trigger engine

- recent context buffer

- chat window frontend

- request/response communication with laptop

Laptop Handles:

- LLM inference

- prompt construction

- response generation

## Setting Up Python 3.11.9 on Raspberry Pi OS 13 (Trixie)
### Prerequisites — install build dependencies:
``` bash
sudo apt update

sudo apt install -y \
  build-essential \
  libssl-dev \
  zlib1g-dev \
  libncurses5-dev \
  libffi-dev \
  libsqlite3-dev \
  libreadline-dev \
  libbz2-dev \
  liblzma-dev \
  tk-dev \
  wget \
  curl
```

### Download and compile Python 3.11.9:
``` bash
cd ~
wget https://www.python.org/ftp/python/3.11.9/Python-3.11.9.tgz
tar -xzf Python-3.11.9.tgz
cd Python-3.11.9
./configure --enable-optimizations --prefix=/usr/local/python3.11
make -j$(nproc)
sudo make altinstall
```
### Verify that tkinter is installed
``` bash
/usr/local/python3.11/bin/python3.11 -c "import tkinter; print('Tk OK')"
```

### Create and activate the venv:
``` bash
cd UAT_On_Edge
/usr/local/python3.11/bin/python3.11 -m venv venv
source venv/bin/activate
```

### Install dependencies:
``` bash
pip install -r requirements.txt
```
