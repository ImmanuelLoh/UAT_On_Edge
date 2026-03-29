import subprocess
import signal
import sys

processes = []

def start_process(cmd, shell=False):
    p = subprocess.Popen(cmd, shell=shell)
    processes.append(p)
    return p

def shutdown(signum, frame):
    print("\n[Launcher] Shutting down all processes...")
    for p in processes:
        try:
            p.terminate()
        except Exception:
            pass
    sys.exit(0)

if __name__ == "__main__":
    signal.signal(signal.SIGINT, shutdown)

    print("[Launcher] Starting Ollama...")
    start_process(["powershell", "-ExecutionPolicy", "Bypass", "-File", "start_ollama_lan.ps1"])

    print("[Launcher] Starting LLM server...")
    start_process(["python", "llm_server.py"])

    print("[Launcher] Running... Press Ctrl+C to stop all.")

    # Keep script alive
    for p in processes:
        p.wait()