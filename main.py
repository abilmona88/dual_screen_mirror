import subprocess
import threading
import time
import signal
import sys
import os

from flask import Flask, Response, render_template

import mss
import numpy as np
import cv2

"""
Dual AirPlay Reflector with Browser View

- Starts two UxPlay receivers (iPhone + iPad)
- Positions their windows at fixed locations
- Streams cropped regions of the screen into a single web page
"""

# ---------------------------------------------------------------------
# CONFIGURATION
# ---------------------------------------------------------------------

IPHONE_NAME = "Reflector-iPhone"
IPAD_NAME = "Reflector-iPad"

# Ports just need to be unique per instance; these match your previous setup.
IPHONE_PORT_BASE = "7100"
IPAD_PORT_BASE = "7200"

# Where on the main display to place the UxPlay windows (macOS points)
# Adjust if needed to better match your screen size.
IPHONE_BOUNDS = {"left": 80, "top": 80, "width": 430, "height": 930}
IPAD_BOUNDS   = {"left": 560, "top": 80, "width": 820, "height": 930}

# Target FPS for browser streams
TARGET_FPS = 24
JPEG_QUALITY = 75

app = Flask(__name__, template_folder="templates", static_folder="static")


# ---------------------------------------------------------------------
# Utility: AppleScript helpers to position UxPlay windows on macOS
# ---------------------------------------------------------------------


def set_window_bounds(title_substring: str, bounds: dict) -> None:
    """
    Use AppleScript (osascript) to move/resize the UxPlay window whose title
    contains `title_substring` (e.g. 'Reflector-iPhone').

    This assumes you're running on macOS and have granted Accessibility
    permissions to Terminal/PyCharm (System Settings -> Privacy & Security).
    """
    x = bounds["left"]
    y = bounds["top"]
    w = bounds["width"]
    h = bounds["height"]

    script = f'''
    tell application "System Events"
        repeat with appName in {{"uxplay", "UxPlay"}}
            if application appName is running then
                tell application process appName
                    repeat with win in windows
                        if name of win contains "{title_substring}" then
                            set position of win to {{{x}, {y}}}
                            set size of win to {{{w}, {h}}}
                        end if
                    end repeat
                end tell
            end if
        end repeat
    end tell
    '''
    try:
        subprocess.run(
            ["osascript", "-e", script],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except FileNotFoundError:
        print("[!] osascript not found. This only works on macOS.", file=sys.stderr)


# ---------------------------------------------------------------------
# UxPlay process management
# ---------------------------------------------------------------------


def start_uxplay_instance(name: str, port_base: str) -> subprocess.Popen:
    """
    Start a single UxPlay instance with:
    - custom AirPlay name
    - distinct port base
    - random MAC address (for multiple instances)
    - vsync disabled to reduce latency
    """
    cmd = [
        "uxplay",
        "-n",
        name,
        "-p",
        port_base,
        "-m",          # random MAC so multiple instances can coexist
        "-vsync",
        "no",
        "-nc",         # do not close window when stream stops
    ]

    print(f"[+] Launching UxPlay: {' '.join(cmd)}")
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    return proc


def tail_process_output(proc: subprocess.Popen, label: str):
    """
    Background thread: tails stdout from a UxPlay process to help with debugging.
    """
    try:
        for line in proc.stdout:
            print(f"[{label}] {{line.rstrip()}}")
    except Exception as e:
        print(f"[{label}] log tailer stopped: {{e}}")


def launch_uxplay_pair():
    iphone_proc = start_uxplay_instance(IPHONE_NAME, IPHONE_PORT_BASE)
    ipad_proc = start_uxplay_instance(IPAD_NAME, IPAD_PORT_BASE)

    # Tail logs in background so you can see what's happening if something breaks
    threading.Thread(
        target=tail_process_output, args=(iphone_proc, "iPhone"), daemon=True
    ).start()
    threading.Thread(
        target=tail_process_output, args=(ipad_proc, "iPad"), daemon=True
    ).start()

    # Give UxPlay time to start and create windows
    print("[*] Waiting a few seconds for UxPlay windows to appear...")
    time.sleep(5)

    # Try to position windows on screen
    print("[*] Positioning windows...")
    set_window_bounds("Reflector-iPhone", IPHONE_BOUNDS)
    set_window_bounds("Reflector-iPad", IPAD_BOUNDS)

    return iphone_proc, ipad_proc


# ---------------------------------------------------------------------
# MJPEG streaming from screen regions (using mss + OpenCV)
# ---------------------------------------------------------------------


def mjpeg_stream(bounds: dict):
    """
    Generator that captures a fixed screen region and yields an MJPEG stream.
    Browsers treat this as a "video" via <img src="/stream/...">.

    NOTE: You must grant Screen Recording permission to the Python process
    for this to work on macOS.
    """
    frame_delay = 1.0 / TARGET_FPS
    with mss.mss() as sct:
        while True:
            img = sct.grab(
                {{
                    "left": bounds["left"],
                    "top": bounds["top"],
                    "width": bounds["width"],
                    "height": bounds["height"],
                }}
            )
            frame = np.array(img)  # BGRA
            frame = cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)

            ok, buf = cv2.imencode(
                ".jpg",
                frame,
                [int(cv2.IMWRITE_JPEG_QUALITY), JPEG_QUALITY],
            )
            if not ok:
                continue

            data = buf.tobytes()
            yield (
                b"--frame\r\n"
                b"Content-Type: image/jpeg\r\n\r\n" + data + b"\r\n"
            )
            time.sleep(frame_delay)


# ---------------------------------------------------------------------
# Flask routes
# ---------------------------------------------------------------------


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/stream/iphone")
def stream_iphone():
    return Response(
        mjpeg_stream(IPHONE_BOUNDS),
        mimetype="multipart/x-mixed-replace; boundary=frame",
    )


@app.route("/stream/ipad")
def stream_ipad():
    return Response(
        mjpeg_stream(IPAD_BOUNDS),
        mimetype="multipart/x-mixed-replace; boundary=frame",
    )


# ---------------------------------------------------------------------
# Main entrypoint
# ---------------------------------------------------------------------


def run_flask():
    # Use 0.0.0.0 so you can hit it from other devices on the LAN if needed
    app.run(host="0.0.0.0", port=8080, threaded=True)


def main():
    iphone_proc, ipad_proc = launch_uxplay_pair()

    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()

    print("")
    print("==== Dual AirPlay Reflector – Browser View ====")
    print("")
    print("1. On your iPhone and iPad, open Control Center -> Screen Mirroring.")
    print(f"2. Choose '{IPHONE_NAME}' on iPhone, '{IPAD_NAME}' on iPad.")
    print("3. On your Mac, open a browser to:  http://localhost:8080")
    print("   You should see both devices side-by-side in one page.")
    print("")
    print("Press Ctrl+C in this terminal to stop everything.")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n[!] Shutting down...")
    finally:
        for proc, label in [(iphone_proc, "iPhone"), (ipad_proc, "iPad")]:
            if proc and proc.poll() is None:
                print(f"[!] Terminating {{label}} UxPlay...")
                try:
                    proc.terminate()
                    proc.wait(timeout=3)
                except Exception:
                    proc.kill()


if __name__ == "__main__":
    if sys.platform != "darwin":
        print("[!] This orchestrator is written for macOS (darwin).", file=sys.stderr)
    main()