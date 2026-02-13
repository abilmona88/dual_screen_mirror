import subprocess
import threading
import time
import signal
import sys
import os
import ctypes

from flask import Flask, Response, render_template
from flask import redirect, request, url_for

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
IPAD_BOUNDS   = {"left": 560, "top": 80, "width": 698, "height": 930}

# Keep the UxPlay windows auto-positioned so you don't need to manually resize/move.
AUTO_POSITION_WINDOWS = True
AUTO_POSITION_INTERVAL_SEC = 2.0

# Target FPS for browser streams
TARGET_FPS = 24
JPEG_QUALITY = 75

app = Flask(__name__, template_folder="templates", static_folder="static")

# ---------------------------------------------------------------------
# Uploads (custom branding)
# ---------------------------------------------------------------------

UPLOADS_DIR = os.path.join(os.path.dirname(__file__), "static", "uploads")
LOGO_FILENAME = "logo.png"
LOGO_PATH = os.path.join(UPLOADS_DIR, LOGO_FILENAME)
MAX_LOGO_BYTES = 5 * 1024 * 1024
MAX_LOGO_DIMENSION = 1024


# ---------------------------------------------------------------------
# macOS window capture (CoreGraphics via ctypes)
# ---------------------------------------------------------------------

_MACOS_CG = None  # lazy-loaded CDLL, False when unavailable
_MACOS_CF = None  # lazy-loaded CDLL
_MACOS_CF_KEYS = None  # cached CFStringRef keys


class _CGPoint(ctypes.Structure):
    _fields_ = [("x", ctypes.c_double), ("y", ctypes.c_double)]


class _CGSize(ctypes.Structure):
    _fields_ = [("width", ctypes.c_double), ("height", ctypes.c_double)]


class _CGRect(ctypes.Structure):
    _fields_ = [("origin", _CGPoint), ("size", _CGSize)]


def _macos_init_window_capture() -> bool:
    global _MACOS_CG, _MACOS_CF, _MACOS_CF_KEYS
    if _MACOS_CG is False:
        return False
    if _MACOS_CG is not None and _MACOS_CF is not None and _MACOS_CF_KEYS is not None:
        return True
    if sys.platform != "darwin":
        _MACOS_CG = False
        return False

    try:
        _MACOS_CG = ctypes.CDLL("/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics")
        _MACOS_CF = ctypes.CDLL(
            "/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation"
        )
    except Exception:
        _MACOS_CG = False
        return False

    # CoreGraphics
    _MACOS_CG.CGWindowListCopyWindowInfo.argtypes = [ctypes.c_uint32, ctypes.c_uint32]
    _MACOS_CG.CGWindowListCopyWindowInfo.restype = ctypes.c_void_p

    _MACOS_CG.CGWindowListCreateImage.argtypes = [
        _CGRect,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_uint32,
    ]
    _MACOS_CG.CGWindowListCreateImage.restype = ctypes.c_void_p

    _MACOS_CG.CGImageGetWidth.argtypes = [ctypes.c_void_p]
    _MACOS_CG.CGImageGetWidth.restype = ctypes.c_size_t
    _MACOS_CG.CGImageGetHeight.argtypes = [ctypes.c_void_p]
    _MACOS_CG.CGImageGetHeight.restype = ctypes.c_size_t
    _MACOS_CG.CGImageGetBytesPerRow.argtypes = [ctypes.c_void_p]
    _MACOS_CG.CGImageGetBytesPerRow.restype = ctypes.c_size_t
    _MACOS_CG.CGImageGetBitsPerPixel.argtypes = [ctypes.c_void_p]
    _MACOS_CG.CGImageGetBitsPerPixel.restype = ctypes.c_size_t
    _MACOS_CG.CGImageGetDataProvider.argtypes = [ctypes.c_void_p]
    _MACOS_CG.CGImageGetDataProvider.restype = ctypes.c_void_p
    _MACOS_CG.CGDataProviderCopyData.argtypes = [ctypes.c_void_p]
    _MACOS_CG.CGDataProviderCopyData.restype = ctypes.c_void_p
    _MACOS_CG.CGImageRelease.argtypes = [ctypes.c_void_p]
    _MACOS_CG.CGImageRelease.restype = None

    # CoreFoundation
    _MACOS_CF.CFStringCreateWithCString.argtypes = [
        ctypes.c_void_p,
        ctypes.c_char_p,
        ctypes.c_uint32,
    ]
    _MACOS_CF.CFStringCreateWithCString.restype = ctypes.c_void_p
    _MACOS_CF.CFStringGetLength.argtypes = [ctypes.c_void_p]
    _MACOS_CF.CFStringGetLength.restype = ctypes.c_long
    _MACOS_CF.CFStringGetMaximumSizeForEncoding.argtypes = [ctypes.c_long, ctypes.c_uint32]
    _MACOS_CF.CFStringGetMaximumSizeForEncoding.restype = ctypes.c_long
    _MACOS_CF.CFStringGetCString.argtypes = [
        ctypes.c_void_p,
        ctypes.c_char_p,
        ctypes.c_long,
        ctypes.c_uint32,
    ]
    _MACOS_CF.CFStringGetCString.restype = ctypes.c_bool
    _MACOS_CF.CFArrayGetCount.argtypes = [ctypes.c_void_p]
    _MACOS_CF.CFArrayGetCount.restype = ctypes.c_long
    _MACOS_CF.CFArrayGetValueAtIndex.argtypes = [ctypes.c_void_p, ctypes.c_long]
    _MACOS_CF.CFArrayGetValueAtIndex.restype = ctypes.c_void_p
    _MACOS_CF.CFDictionaryGetValue.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    _MACOS_CF.CFDictionaryGetValue.restype = ctypes.c_void_p
    _MACOS_CF.CFNumberGetValue.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p]
    _MACOS_CF.CFNumberGetValue.restype = ctypes.c_bool
    _MACOS_CF.CFDataGetLength.argtypes = [ctypes.c_void_p]
    _MACOS_CF.CFDataGetLength.restype = ctypes.c_long
    _MACOS_CF.CFDataGetBytePtr.argtypes = [ctypes.c_void_p]
    _MACOS_CF.CFDataGetBytePtr.restype = ctypes.c_void_p
    _MACOS_CF.CFRelease.argtypes = [ctypes.c_void_p]
    _MACOS_CF.CFRelease.restype = None

    kCFStringEncodingUTF8 = 0x08000100
    def _key(name: str) -> ctypes.c_void_p:
        return _MACOS_CF.CFStringCreateWithCString(None, name.encode("utf-8"), kCFStringEncodingUTF8)

    _MACOS_CF_KEYS = {
        "kCGWindowName": _key("kCGWindowName"),
        "kCGWindowOwnerName": _key("kCGWindowOwnerName"),
        "kCGWindowNumber": _key("kCGWindowNumber"),
    }
    return True


def _macos_cfstring_to_str(cf_string: int) -> str:
    if not cf_string:
        return ""
    if not _macos_init_window_capture():
        return ""

    kCFStringEncodingUTF8 = 0x08000100
    length = _MACOS_CF.CFStringGetLength(cf_string)
    max_size = _MACOS_CF.CFStringGetMaximumSizeForEncoding(length, kCFStringEncodingUTF8) + 1
    buf = ctypes.create_string_buffer(max(2, int(max_size)))
    ok = _MACOS_CF.CFStringGetCString(cf_string, buf, len(buf), kCFStringEncodingUTF8)
    if not ok:
        return ""
    return buf.value.decode("utf-8", errors="replace")


def macos_find_window_id(title_substring: str) -> int | None:
    if not _macos_init_window_capture():
        return None

    kCGWindowListOptionAll = 0
    window_info = _MACOS_CG.CGWindowListCopyWindowInfo(kCGWindowListOptionAll, 0)
    if not window_info:
        return None

    needle = (title_substring or "").lower()
    try:
        count = _MACOS_CF.CFArrayGetCount(window_info)
        for i in range(int(count)):
            info = _MACOS_CF.CFArrayGetValueAtIndex(window_info, i)
            if not info:
                continue

            name_ref = _MACOS_CF.CFDictionaryGetValue(
                info, _MACOS_CF_KEYS["kCGWindowName"]
            )
            name = _macos_cfstring_to_str(name_ref)
            if needle and needle not in name.lower():
                continue

            number_ref = _MACOS_CF.CFDictionaryGetValue(
                info, _MACOS_CF_KEYS["kCGWindowNumber"]
            )
            if not number_ref:
                continue

            num = ctypes.c_int(0)
            kCFNumberIntType = 9
            ok = _MACOS_CF.CFNumberGetValue(number_ref, kCFNumberIntType, ctypes.byref(num))
            if ok:
                return int(num.value)
    finally:
        _MACOS_CF.CFRelease(window_info)

    return None


def macos_capture_window_bgr(window_id: int) -> np.ndarray | None:
    if not _macos_init_window_capture():
        return None

    kCGWindowListOptionIncludingWindow = 8
    kCGWindowImageBoundsIgnoreFraming = 1
    rect_null = _CGRect(
        _CGPoint(float("inf"), float("inf")),
        _CGSize(0.0, 0.0),
    )

    image = _MACOS_CG.CGWindowListCreateImage(
        rect_null,
        kCGWindowListOptionIncludingWindow,
        int(window_id),
        kCGWindowImageBoundsIgnoreFraming,
    )
    if not image:
        return None

    try:
        width = int(_MACOS_CG.CGImageGetWidth(image))
        height = int(_MACOS_CG.CGImageGetHeight(image))
        if width <= 0 or height <= 0:
            return None

        bits_per_pixel = int(_MACOS_CG.CGImageGetBitsPerPixel(image))
        if bits_per_pixel != 32:
            return None

        bytes_per_row = int(_MACOS_CG.CGImageGetBytesPerRow(image))
        provider = _MACOS_CG.CGImageGetDataProvider(image)
        data_ref = _MACOS_CG.CGDataProviderCopyData(provider)
        if not data_ref:
            return None

        try:
            length = int(_MACOS_CF.CFDataGetLength(data_ref))
            ptr = _MACOS_CF.CFDataGetBytePtr(data_ref)
            if not ptr or length <= 0:
                return None
            raw = ctypes.string_at(ptr, length)
        finally:
            _MACOS_CF.CFRelease(data_ref)

        arr = np.frombuffer(raw, dtype=np.uint8)
        arr = arr.reshape((height, bytes_per_row))
        arr = arr[:, : width * 4]
        arr = arr.reshape((height, width, 4))  # BGRA
        return cv2.cvtColor(arr, cv2.COLOR_BGRA2BGR)
    finally:
        _MACOS_CG.CGImageRelease(image)


# ---------------------------------------------------------------------
# Utility: AppleScript helpers to position UxPlay windows on macOS
# ---------------------------------------------------------------------


def set_window_bounds(title_substring: str, bounds: dict) -> bool:
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
    set didSet to false
    tell application "System Events"
        repeat with appName in {{"uxplay", "UxPlay"}}
            if application appName is running then
                tell application process appName
                    repeat with win in windows
                        if name of win contains "{title_substring}" then
                            set position of win to {{{x}, {y}}}
                            set size of win to {{{w}, {h}}}
                            set didSet to true
                        end if
                    end repeat
                end tell
            end if
        end repeat
    end tell
    return didSet
    '''
    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            check=False,
            capture_output=True,
            text=True,
        )
        return result.stdout.strip().lower() == "true"
    except FileNotFoundError:
        print("[!] osascript not found. This only works on macOS.", file=sys.stderr)
        return False


def keep_uxplay_windows_positioned(stop_event: threading.Event):
    if sys.platform != "darwin":
        return
    if not AUTO_POSITION_WINDOWS:
        return

    iphone_done = False
    ipad_done = False
    while not stop_event.is_set():
        if not iphone_done:
            iphone_done = set_window_bounds(IPHONE_NAME, IPHONE_BOUNDS)
        if not ipad_done:
            ipad_done = set_window_bounds(IPAD_NAME, IPAD_BOUNDS)

        if iphone_done and ipad_done:
            return

        time.sleep(AUTO_POSITION_INTERVAL_SEC)


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

    # Keep windows positioned even if they appear later (after a device connects).
    stop_event = threading.Event()
    threading.Thread(
        target=keep_uxplay_windows_positioned,
        args=(stop_event,),
        daemon=True,
    ).start()

    return iphone_proc, ipad_proc, stop_event


# ---------------------------------------------------------------------
# MJPEG streaming from screen regions (using mss + OpenCV)
# ---------------------------------------------------------------------


def mjpeg_stream(bounds: dict, window_title_substring: str | None = None):
    """
    Generator that captures a fixed screen region and yields an MJPEG stream.
    Browsers treat this as a "video" via <img src="/stream/...">.

    NOTE: You must grant Screen Recording permission to the Python process
    for this to work on macOS.
    """
    frame_delay = 1.0 / TARGET_FPS
    window_id = None
    last_window_lookup = 0.0
    with mss.mss() as sct:
        while True:
            frame = None

            if window_title_substring and sys.platform == "darwin":
                now = time.time()
                if window_id is None and (now - last_window_lookup) >= 0.8:
                    window_id = macos_find_window_id(window_title_substring)
                    last_window_lookup = now

                if window_id is not None:
                    frame = macos_capture_window_bgr(window_id)
                    if frame is None:
                        window_id = None

            if frame is None:
                img = sct.grab(
                    {
                        "left": bounds["left"],
                        "top": bounds["top"],
                        "width": bounds["width"],
                        "height": bounds["height"],
                    }
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
    logo_url = None
    if os.path.exists(LOGO_PATH):
        try:
            mtime = int(os.path.getmtime(LOGO_PATH))
            logo_url = url_for(
                "static",
                filename=f"uploads/{LOGO_FILENAME}",
                v=mtime,
            )
        except OSError:
            logo_url = url_for(
                "static",
                filename=f"uploads/{LOGO_FILENAME}",
            )

    return render_template(
        "index.html",
        logo_url=logo_url,
        iphone_name=IPHONE_NAME,
        ipad_name=IPAD_NAME,
    )


@app.route("/logo", methods=["POST"])
def upload_logo():
    file = request.files.get("logo")
    if not file:
        return redirect(url_for("index"))

    data = file.read()
    if not data:
        return redirect(url_for("index"))

    if len(data) > MAX_LOGO_BYTES:
        return redirect(url_for("index"))

    arr = np.frombuffer(data, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_UNCHANGED)
    if img is None:
        return redirect(url_for("index"))

    height, width = img.shape[:2]
    max_dim = max(height, width)
    if max_dim > MAX_LOGO_DIMENSION:
        scale = MAX_LOGO_DIMENSION / float(max_dim)
        new_w = max(1, int(round(width * scale)))
        new_h = max(1, int(round(height * scale)))
        img = cv2.resize(img, (new_w, new_h), interpolation=cv2.INTER_AREA)

    os.makedirs(UPLOADS_DIR, exist_ok=True)
    cv2.imwrite(LOGO_PATH, img)
    return redirect(url_for("index"))


@app.route("/logo/clear", methods=["POST"])
def clear_logo():
    try:
        os.remove(LOGO_PATH)
    except FileNotFoundError:
        pass
    return redirect(url_for("index"))


@app.route("/stream/iphone")
def stream_iphone():
    return Response(
        mjpeg_stream(IPHONE_BOUNDS, IPHONE_NAME),
        mimetype="multipart/x-mixed-replace; boundary=frame",
    )


@app.route("/stream/ipad")
def stream_ipad():
    return Response(
        mjpeg_stream(IPAD_BOUNDS, IPAD_NAME),
        mimetype="multipart/x-mixed-replace; boundary=frame",
    )


# ---------------------------------------------------------------------
# Main entrypoint
# ---------------------------------------------------------------------


def run_flask():
    # Use 0.0.0.0 so you can hit it from other devices on the LAN if needed
    app.run(host="0.0.0.0", port=8080, threaded=True)


def main():
    iphone_proc, ipad_proc, window_stop_event = launch_uxplay_pair()

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
        window_stop_event.set()
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
