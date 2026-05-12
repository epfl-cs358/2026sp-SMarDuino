"""
pnp_bottom_vision.py
====================
Live camera UI for the pick-and-place orientation system.

All detection logic lives in orientation_engine.py.
This file handles only: camera loop, display, keyboard shortcuts, Arduino serial.

Usage:
    python pnp_bottom_vision.py

    Pass a .kicad_pcb file or a centroid CSV as PCB_FILE below.

Keyboard shortcuts:
    SPACE      — analyse current frame & send result to Arduino
    n / +      — next component in placement list
    -          — previous component
    d          — toggle debug overlay
    q          — quit

Dependencies:
    pip install opencv-python pyserial numpy
"""

import cv2
import numpy as np
import serial
import time
from typing import Optional

from orientation_engine import (
    load_pcb,
    find_nozzle_component,
    check_orientation,
    OrientationResult,
    FRAME_WIDTH, FRAME_HEIGHT, CENTER_RADIUS,
)

# ── Configuration ─────────────────────────────────────────────────────────────
CAMERA_INDEX = 1           # Change if the HutoPi is not on index 0
SERIAL_PORT  = "COM3"
BAUD_RATE    = 115200
SHOW_DEBUG   = True

PCB_FILE     = "flashing-led.kicad_pcb"   # or "pcb_centroid.csv"


# ── Colours for each component type ──────────────────────────────────────────
_COLORS = {
    "RESISTANCE":   ( 50, 255, 100),
    "CONDENSATEUR": (  0, 160, 255),
    "LED":          (  0,  80, 255),
    "SMD_PASSIVE":  (255,   0, 200),
    "IC":           (  0, 220, 220),
    "ELCAP":        (  0, 200, 255),
    "CONNECTOR":    (255, 140,   0),
    "INCONNU":      (130, 130, 130),
}


# ── Visualisation ─────────────────────────────────────────────────────────────

def draw_result(frame: np.ndarray,
                result: Optional[OrientationResult],
                designator: str) -> np.ndarray:
    out = frame.copy()
    H, W = out.shape[:2]
    cx0, cy0 = W // 2, H // 2

    # Centre crosshair and search-zone circle
    cv2.line(out,   (cx0 - 50, cy0), (cx0 + 50, cy0), (60, 60, 60), 1)
    cv2.line(out,   (cx0, cy0 - 50), (cx0, cy0 + 50), (60, 60, 60), 1)
    cv2.circle(out, (cx0, cy0), CENTER_RADIUS, (40, 40, 40), 1)
    cv2.putText(out, f"Target: {designator}", (W - 270, 35),
                cv2.FONT_HERSHEY_SIMPLEX, 0.9, (220, 220, 220), 2)

    if result is None:
        cv2.putText(out, "No component detected — hold part under camera",
                    (20, 50), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 100, 255), 2)
        return out

    comp    = result.component
    color   = _COLORS.get(comp.comp_type, (255, 255, 255))
    s_color = (0, 230, 0) if result.ok else (0, 80, 255)
    if result.action == "REJECT":
        s_color = (0, 0, 255)

    # Bounding box
    bx = comp.cx - comp.width  // 2
    by = comp.cy - comp.height // 2
    cv2.rectangle(out, (bx, by), (bx + comp.width, by + comp.height), color, 2)
    cv2.circle(out, (comp.cx, comp.cy), 5, color, -1)

    # Detected angle arrow (yellow)
    ang = np.radians(comp.angle)
    cv2.arrowedLine(out, (comp.cx, comp.cy),
                    (int(comp.cx + 70 * np.cos(ang)),
                     int(comp.cy + 70 * np.sin(ang))),
                    color, 2, tipLength=0.3)

    # Required angle arrow (cyan) when PCB data is available
    if result.pcb_comp:
        req = np.radians(result.required_angle)
        cv2.arrowedLine(out, (comp.cx, comp.cy),
                        (int(comp.cx + 70 * np.cos(req)),
                         int(comp.cy + 70 * np.sin(req))),
                        (0, 230, 230), 2, tipLength=0.3)

    # Pad circles
    for px, py in comp.pad_positions:
        cv2.circle(out, (px, py), 7, (0, 255, 255), 2)

    # Info panel
    lines = [
        f"Type    : {comp.comp_type}",
        f"Conf    : {comp.confidence * 100:.0f}%",
        f"Pads    : {comp.pad_count}",
        f"Method  : {comp.angle_method}",
        f"Current : {comp.angle:.1f} deg",
        f"Required: {result.required_angle:.1f} deg",
        f"Delta   : {result.delta_angle:+.1f} deg",
        f"Action  : {result.action}",
    ]
    panel_h = len(lines) * 28 + 20
    cv2.rectangle(out, (8, 8), (400, 8 + panel_h), (0, 0, 0), -1)
    cv2.rectangle(out, (8, 8), (400, 8 + panel_h), s_color, 2)
    for i, ln in enumerate(lines):
        cv2.putText(out, ln, (18, 36 + i * 28),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6,
                    s_color if i == 7 else (240, 240, 240), 1)

    # Bottom banner
    if result.ok:
        banner = "  PLACE NOW  "
    elif result.action == "REJECT":
        banner = f"  REJECT — {result.message}  "
    else:
        banner = f"  {result.action}  {abs(result.delta_angle):.1f} deg  "

    cv2.rectangle(out, (0, H - 55), (W, H), s_color, -1)
    cv2.putText(out, banner, (20, H - 16),
                cv2.FONT_HERSHEY_SIMPLEX, 0.95, (0, 0, 0), 2)

    return out


def draw_debug_overlay(frame: np.ndarray, mask: np.ndarray) -> np.ndarray:
    """Green overlay = detected foreground. Red warning if threshold is too broad."""
    overlay     = frame.copy()
    green_mask  = mask > 0
    coverage    = np.count_nonzero(green_mask) / green_mask.size

    if coverage > 0.55:
        cv2.putText(overlay,
                    f"Threshold too broad ({coverage * 100:.0f}% detected) — adjust trackbar",
                    (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (0, 0, 255), 2)
    else:
        overlay[green_mask] = (
            overlay[green_mask] * 0.4 + np.array([0, 255, 80]) * 0.6
        ).astype(np.uint8)

    return overlay


# ── Arduino serial ────────────────────────────────────────────────────────────

class ArduinoSerial:
    def __init__(self, port: str, baud: int):
        self.ser: Optional[serial.Serial] = None
        try:
            self.ser = serial.Serial(port, baud, timeout=1)
            time.sleep(2)
            print(f"[Serial] Connected on {port} @ {baud}")
        except Exception as e:
            print(f"[Serial] {e}  →  simulation mode active")

    def send(self, result: OrientationResult) -> bool:
        msg = result.to_json()
        if not self.ser or not self.ser.is_open:
            print(f"[SIM] → Arduino: {msg.strip()}")
            return True
        try:
            self.ser.write(msg.encode("utf-8"))
            return True
        except Exception as e:
            print(f"[Serial] Send error: {e}")
            return False

    def read_response(self) -> Optional[str]:
        if not self.ser or not self.ser.is_open:
            return None
        try:
            if self.ser.in_waiting:
                return self.ser.readline().decode("utf-8").strip()
        except Exception:
            pass
        return None

    def close(self):
        if self.ser and self.ser.is_open:
            self.ser.close()


# ── Main loop ─────────────────────────────────────────────────────────────────

def main():
    print("=" * 55)
    print("  Vision PnP — Bottom View + Orientation Check")
    print("=" * 55)
    print(f"  PCB file : {PCB_FILE}")
    print()
    print("  Keyboard shortcuts:")
    print("   SPACE  → analyse & send to Arduino")
    print("   n / +  → next component in list")
    print("   -      → previous component")
    print("   d      → toggle debug overlay")
    print("   q      → quit")
    print()
    print("  Threshold trackbar in debug window:")
    print("   0 = Otsu auto   |   1-255 = fixed value")
    print("=" * 55)

    pcb_data    = load_pcb(PCB_FILE)
    designators = sorted(pcb_data.keys())
    current_idx = 0

    def current_des() -> str:
        return designators[current_idx % len(designators)] if designators else "??"

    # Open camera with CAP_DSHOW (required on Windows for reliable operation)
    cap = cv2.VideoCapture(CAMERA_INDEX, cv2.CAP_DSHOW)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH,  FRAME_WIDTH)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, FRAME_HEIGHT)
    cap.set(cv2.CAP_PROP_AUTOFOCUS,    0)    # disable autofocus — fix focus at component distance
    cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, 1)   # 1 = manual exposure mode
    cap.set(cv2.CAP_PROP_EXPOSURE,    -6)    # short exposure = less motion blur; raise to -4 if too dark
    cap.set(cv2.CAP_PROP_BRIGHTNESS,  150)   # compensate for shorter exposure

    if not cap.isOpened():
        print(f"[ERROR] Cannot open camera index {CAMERA_INDEX}")
        print("        Try changing CAMERA_INDEX to 1 or 2")
        return

    arduino    = ArduinoSerial(SERIAL_PORT, BAUD_RATE)
    show_debug = SHOW_DEBUG

    DEBUG_WIN  = "Debug — colour overlay"
    THRESH_BAR = "Threshold (0=auto)"

    if show_debug:
        cv2.namedWindow(DEBUG_WIN)
        cv2.createTrackbar(THRESH_BAR, DEBUG_WIN, 0, 255, lambda _: None)

    last_result: Optional[OrientationResult] = None

    while True:
        ret, frame = cap.read()
        if not ret:
            print("[ERROR] No frame received")
            break

        manual_thresh = 0
        if show_debug:
            try:
                manual_thresh = cv2.getTrackbarPos(THRESH_BAR, DEBUG_WIN)
            except Exception:
                pass

        des        = current_des()
        comp, mask = find_nozzle_component(frame, manual_thresh)
        pcb_comp   = pcb_data.get(des)
        last_result = check_orientation(comp, pcb_comp) if comp else None

        cv2.imshow("Vision PnP — Bottom View", draw_result(frame, last_result, des))

        if show_debug:
            cv2.imshow(DEBUG_WIN, draw_debug_overlay(frame, mask))

        key = cv2.waitKey(1) & 0xFF

        if key == ord("q"):
            break
        elif key == ord("d"):
            show_debug = not show_debug
            if show_debug:
                cv2.namedWindow(DEBUG_WIN)
                cv2.createTrackbar(THRESH_BAR, DEBUG_WIN, 0, 255, lambda _: None)
            else:
                cv2.destroyWindow(DEBUG_WIN)
        elif key in (ord("n"), ord("+")):
            current_idx += 1
            print(f"[Nav] → {current_des()}")
        elif key == ord("-"):
            current_idx = max(0, current_idx - 1)
            print(f"[Nav] → {current_des()}")
        elif key == ord(" "):
            if last_result:
                arduino.send(last_result)
                print(f"[SENT]  {last_result.message}")
                print(f"[JSON]  {last_result.to_json().strip()}")
                time.sleep(0.05)
                rep = arduino.read_response()
                if rep:
                    print(f"[Arduino] {rep}")
                if last_result.action == "PLACE" and designators:
                    current_idx += 1
                    print(f"[Nav] Auto-advance → {current_des()}")
            else:
                print("[Info] No component detected — hold component under camera")

    cap.release()
    arduino.close()
    cv2.destroyAllWindows()
    print("Vision stopped.")


if __name__ == "__main__":
    main()
