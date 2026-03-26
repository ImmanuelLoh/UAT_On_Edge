#!/bin/bash
# run_perf.sh
# PASO - hardware-level profiling using Linux perf stat.
#
# Captures: CPU cycles, instructions, IPC, cache misses, branch mispredictions.
#
# Requirements:
#   sudo apt install linux-perf   (if not already installed)
#   Run as root or with sudo
#
# Usage (from repo root):
#   chmod +x rpi_a/benchmarks/run_perf.sh
#   sudo ./rpi_a/benchmarks/run_perf.sh before
#   sudo ./rpi_a/benchmarks/run_perf.sh after
#   sudo ./rpi_a/benchmarks/run_perf.sh e2e <RECEIVER_IP> <LABEL>

MODE=$1

PERF_EVENTS="cycles,instructions,cache-references,cache-misses,branch-misses"

mkdir -p results

case "$MODE" in

  # ---------------------------------------------------------------------------
  # MODULE LEVEL -- BEFORE (blocking cap.read, no thread)
  # ---------------------------------------------------------------------------
  before)
    echo ""
    echo "======================================================"
    echo " perf stat -- BEFORE (face_sensor_before)"
    echo "======================================================"
    echo "Full pipeline with blocking cap.read(), no CameraStream thread."
    echo "Running for ~50s (calibration + 5s warmup + 30s measure)..."
    echo "Complete calibration steps when prompted."
    echo ""
    perf stat \
      -e $PERF_EVENTS \
      -- python3 rpi_a/benchmarks/face_sensor_before.py \
      2>&1 | tee results/perf_before.txt
    echo ""
    echo "Results saved to results/perf_before.txt"
    ;;

  # ---------------------------------------------------------------------------
  # MODULE LEVEL -- AFTER (CameraStream threaded, full face pipeline)
  # ---------------------------------------------------------------------------
  after)
    echo ""
    echo "============================================="
    echo " perf stat -- AFTER (face_sensor_after)"
    echo "============================================="
    echo "Running for ~50s (calibration + 5s warmup + 30s measure)..."
    echo "Complete calibration steps when prompted."
    echo ""
    perf stat \
      -e $PERF_EVENTS \
      -- python3 rpi_a/benchmarks/face_sensor_after.py \
      2>&1 | tee results/perf_after.txt
    echo ""
    echo "Results saved to results/perf_after.txt"
    ;;

  # ---------------------------------------------------------------------------
  # E2E -- full tracker_bridge system
  # ---------------------------------------------------------------------------
  e2e)
    RECEIVER_IP=$2
    LABEL=$3

    if [ -z "$RECEIVER_IP" ] || [ -z "$LABEL" ]; then
      echo "Usage: sudo ./rpi_a/benchmarks/run_perf.sh e2e <RECEIVER_IP> <LABEL>"
      exit 1
    fi

    echo ""
    echo "=================================================="
    echo " perf stat -- E2E (tracker_bridge_benchmark)"
    echo "=================================================="
    echo "Running full system for ~80s (calibration + 60s measure)..."
    echo "Press Ctrl+C to stop early -- summary will print on exit."
    echo ""
    perf stat \
      -e $PERF_EVENTS \
      -- python3 rpi_a/benchmarks/tracker_bridge_benchmark.py "$RECEIVER_IP" "$LABEL" \
      2>&1 | tee results/perf_e2e.txt
    echo ""
    echo "Results saved to results/perf_e2e.txt"
    ;;

  *)
    echo "Usage: sudo ./rpi_a/benchmarks/run_perf.sh [before|after|e2e <IP> <LABEL>]"
    echo ""
    echo "  before  -- profiles face_sensor_before.py (blocking cap.read)"
    echo "  after   -- profiles face_sensor_after.py (CameraStream threaded)"
    echo "  e2e     -- profiles full tracker_bridge system"
    exit 1
    ;;
esac
