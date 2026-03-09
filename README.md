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
