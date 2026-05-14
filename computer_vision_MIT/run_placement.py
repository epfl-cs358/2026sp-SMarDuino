"""
run_placement.py
================
Automated placement CLI — runs the full vision-check sequence end-to-end.

This replaces the manual n/+/- navigation in pnp_bottom_vision.py for production
runs.  pnp_bottom_vision.py stays in place as the manual debug/tuning tool.

Usage:
    python run_placement.py

Keyboard shortcuts:
    SPACE  — process the current expected designator (capture → detect → send → ACK)
    s      — skip current component (operator override after a WRONG_PART halt)
    r      — reset to start of queue
    q      — quit

Configuration:
    Edit the constants below (CAMERA_INDEX, SERIAL_PORT, PCB_FILE).
"""

import cv2
import numpy as np
import time
from typing import Optional

from orientation_engine import (
    OrientationResult,
    FRAME_WIDTH, FRAME_HEIGHT, CENTER_RADIUS,
)
from placement_sequencer import PlacementSequencer, SequencerStatus, SequencerResult

# ── Configuration ─────────────────────────────────────────────────────────────
CAMERA_INDEX = 1
SERIAL_PORT  = "COM3"
BAUD_RATE    = 115200
PCB_FILE     = r"C:\Users\Admin\Downloads\flashing-led-all.pos"
VERBOSE      = True


# ── Colours ───────────────────────────────────────────────────────────────────
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

_STATUS_COLORS = {
    SequencerStatus.OK_PLACED:      (0, 230,   0),
    SequencerStatus.NEEDS_ROTATION: (0, 160, 255),
    SequencerStatus.WRONG_PART:     (0,   0, 255),
    SequencerStatus.NO_DETECTION:   (0, 100, 255),
    SequencerStatus.RETRY:          (0, 200, 200),
}


# ── Drawing helpers ───────────────────────────────────────────────────────────

def draw_result(frame: np.ndarray,
                seq_result: Optional[SequencerResult],
                designator: str) -> np.ndarray:
    out = frame.copy()
    H, W = out.shape[:2]
    cx0, cy0 = W // 2, H // 2

    cv2.line(out,   (cx0 - 50, cy0), (cx0 + 50, cy0), (60, 60, 60), 1)
    cv2.line(out,   (cx0, cy0 - 50), (cx0, cy0 + 50), (60, 60, 60), 1)
    cv2.circle(out, (cx0, cy0), CENTER_RADIUS, (40, 40, 40), 1)
    cv2.putText(out, f"Target: {designator}", (W - 270, 35),
                cv2.FONT_HERSHEY_SIMPLEX, 0.9, (220, 220, 220), 2)

    if seq_result is None or seq_result.orientation is None:
        msg = "Hold component under camera, then press SPACE"
        if seq_result and seq_result.status == SequencerStatus.NO_DETECTION:
            msg = f"No detection — {seq_result.message}"
        elif seq_result and seq_result.status == SequencerStatus.WRONG_PART:
            msg = f"WRONG PART — {seq_result.message}  (s=skip)"
        cv2.putText(out, msg, (20, 50),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.65, (0, 100, 255), 2)
        return out

    result = seq_result.orientation
    comp   = result.component
    color  = _COLORS.get(comp.comp_type, (255, 255, 255))
    s_color = _STATUS_COLORS.get(seq_result.status, (200, 200, 200))

    bx = comp.cx - comp.width  // 2
    by = comp.cy - comp.height // 2
    cv2.rectangle(out, (bx, by), (bx + comp.width, by + comp.height), color, 2)
    cv2.circle(out, (comp.cx, comp.cy), 5, color, -1)

    ang = np.radians(comp.angle)
    cv2.arrowedLine(out, (comp.cx, comp.cy),
                    (int(comp.cx + 70 * np.cos(ang)),
                     int(comp.cy + 70 * np.sin(ang))),
                    color, 2, tipLength=0.3)

    if result.pcb_comp:
        req = np.radians(result.required_angle)
        cv2.arrowedLine(out, (comp.cx, comp.cy),
                        (int(comp.cx + 70 * np.cos(req)),
                         int(comp.cy + 70 * np.sin(req))),
                        (0, 230, 230), 2, tipLength=0.3)

    for px, py in comp.pad_positions:
        cv2.circle(out, (px, py), 7, (0, 255, 255), 2)

    pin1_txt = f"  pin1={comp.pin1_side}" if comp.pad_count >= 6 else ""
    lines = [
        f"Type    : {comp.comp_type}",
        f"Conf    : {comp.confidence * 100:.0f}%",
        f"Pads    : {comp.pad_count}{pin1_txt}",
        f"Method  : {comp.angle_method}",
        f"Current : {comp.angle:.1f} deg",
        f"Required: {result.required_angle:.1f} deg",
        f"Delta   : {result.delta_angle:+.1f} deg",
        f"Status  : {seq_result.status.name}",
    ]
    panel_h = len(lines) * 28 + 20
    cv2.rectangle(out, (8, 8), (420, 8 + panel_h), (0, 0, 0), -1)
    cv2.rectangle(out, (8, 8), (420, 8 + panel_h), s_color, 2)
    for i, ln in enumerate(lines):
        cv2.putText(out, ln, (18, 36 + i * 28),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6,
                    s_color if i == 7 else (240, 240, 240), 1)

    if seq_result.status == SequencerStatus.OK_PLACED:
        banner = f"  PLACED {designator}  "
    elif seq_result.status == SequencerStatus.WRONG_PART:
        banner = f"  WRONG PART / REJECT  (s=skip)  "
    elif seq_result.status in (SequencerStatus.NEEDS_ROTATION,
                                SequencerStatus.RETRY):
        banner = f"  {result.action}  {abs(result.delta_angle):.1f}°  — re-press SPACE  "
    else:
        banner = f"  {seq_result.message}  "

    cv2.rectangle(out, (0, H - 55), (W, H), s_color, -1)
    cv2.putText(out, banner, (20, H - 16),
                cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 0, 0), 2)

    return out


def draw_debug_overlay(frame: np.ndarray, mask: np.ndarray) -> np.ndarray:
    overlay  = frame.copy()
    coverage = np.count_nonzero(mask) / mask.size
    if coverage > 0.55:
        cv2.putText(overlay,
                    f"Threshold too broad ({coverage * 100:.0f}% detected) — adjust camera",
                    (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (0, 0, 255), 2)
    else:
        gm = mask > 0
        overlay[gm] = (overlay[gm] * 0.4 + np.array([0, 255, 80]) * 0.6).astype(np.uint8)
    return overlay


def draw_pad_debug(frame: np.ndarray, pipeline, comp) -> np.ndarray:
    """
    Zoomed debug window showing:
      - Real camera image (greyscale) cropped to the component area
      - Pad mask overlaid in blue — every blob the threshold sees
      - Green circles + numbers at each confirmed pad centroid
      - Cyan arrow for the detected orientation angle
      - Orange dot for the component centre

    Blue = what the threshold captured (including noise / body)
    Green circles = pads that passed the area filter (PAD_MIN_AREA / PAD_MAX_AREA)

    Crop and zoom scale automatically with the detected component size so that
    0805 passives get ~4x magnification while large ICs fit entirely in the window.
    """
    OUT_SIZE = 480   # fixed output window size in pixels

    pad_mask = getattr(pipeline, '_last_pad_mask', None)

    if comp is None or pad_mask is None:
        blank = np.zeros((OUT_SIZE, OUT_SIZE, 3), dtype=np.uint8)
        cv2.putText(blank, "No detection", (20, OUT_SIZE // 2),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, (80, 80, 80), 2)
        return blank

    # Fit crop to the component: half-size = component radius + 50px margin
    raw_half = max(comp.width, comp.height) // 2 + 50
    # ZOOM: highest magnification that keeps output at OUT_SIZE; minimum 1x
    ZOOM = max(1, OUT_SIZE // (raw_half * 2))
    # Snap HALF so that crop × zoom == OUT_SIZE exactly (clean pixel grid)
    HALF = OUT_SIZE // (2 * ZOOM)

    H, W = frame.shape[:2]
    cx, cy = comp.cx, comp.cy
    x1 = max(0, cx - HALF);  y1 = max(0, cy - HALF)
    x2 = min(W, cx + HALF);  y2 = min(H, cy + HALF)

    # Greyscale crop of real image
    gray_crop = cv2.cvtColor(frame[y1:y2, x1:x2], cv2.COLOR_BGR2GRAY)
    base      = cv2.cvtColor(gray_crop, cv2.COLOR_GRAY2BGR)

    # Overlay pad mask as semi-transparent blue
    pm_crop = pad_mask[y1:y2, x1:x2]
    hot     = pm_crop > 0
    base[hot] = (base[hot] * 0.35 + np.array([200, 80, 0]) * 0.65).astype(np.uint8)

    # Upscale for readability
    interp = cv2.INTER_NEAREST if ZOOM >= 2 else cv2.INTER_LINEAR
    out    = cv2.resize(base, (OUT_SIZE, OUT_SIZE), interpolation=interp)

    # Component centre (orange dot)
    ocx = int((cx - x1) * ZOOM)
    ocy = int((cy - y1) * ZOOM)
    cv2.circle(out, (ocx, ocy), 5, (0, 140, 255), -1)

    # Detected angle arrow (cyan) — length scales with zoom
    ang     = np.radians(comp.angle)
    arr_len = max(30, int(55 * ZOOM / 4))
    cv2.arrowedLine(out, (ocx, ocy),
                    (int(ocx + arr_len * np.cos(ang)),
                     int(ocy + arr_len * np.sin(ang))),
                    (0, 220, 220), 2, tipLength=0.25)

    # Pad circles + labels (only draw pads that fall inside the crop window)
    pad_r = max(4, int(10 * ZOOM / 4))
    for i, (px, py) in enumerate(comp.pad_positions):
        ppx = int((px - x1) * ZOOM)
        ppy = int((py - y1) * ZOOM)
        if 0 <= ppx < OUT_SIZE and 0 <= ppy < OUT_SIZE:
            cv2.circle(out, (ppx, ppy), pad_r, (0, 255, 0), 2)
            cv2.putText(out, f"P{i + 1}", (ppx + pad_r, ppy - pad_r),
                        cv2.FONT_HERSHEY_SIMPLEX, max(0.35, 0.45 * ZOOM / 4),
                        (0, 255, 0), 1)

    # Header
    method_color = (0, 220, 220) if comp.angle_method == "pads_led" else (200, 200, 200)
    color_hint   = getattr(pipeline, '_last_color_hint', 'unknown')
    color_label  = {"blue_body": "BLUE=cap", "teal_marker": "TEAL=led",
                    "dark_body": "DARK=res"}.get(color_hint, color_hint)
    cv2.putText(out,
                f"{len(comp.pad_positions)} pads | {comp.angle_method} | {comp.angle:.1f}deg | {color_label}",
                (6, 18), cv2.FONT_HERSHEY_SIMPLEX, 0.44, method_color, 1)
    cv2.putText(out, f"zoom {ZOOM}x  crop {HALF*2}px", (6, OUT_SIZE - 8),
                cv2.FONT_HERSHEY_SIMPLEX, 0.38, (80, 80, 80), 1)
    if comp.angle_method == "pads_led":
        cv2.putText(out, "cathode resolved (arrow)", (6, 34),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.42, (0, 220, 220), 1)
    elif comp.angle_method == "pads":
        cv2.putText(out, "axis only (180 deg ambiguous)", (6, 34),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.42, (0, 160, 255), 1)
    elif comp.angle_method in ("pca", "bbox"):
        cv2.putText(out, "NO PADS - angle unreliable", (6, 34),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.42, (0, 0, 255), 1)

    return out


# ── Main loop ─────────────────────────────────────────────────────────────────

def main():
    print("=" * 60)
    print("  Vision PnP — Automated Placement Sequencer")
    print("=" * 60)
    print(f"  PCB file : {PCB_FILE}")
    print()
    print("  SPACE  → process current component")
    print("  s      → skip (after WRONG_PART halt)")
    print("  r      → reset queue to start")
    print("  d      → toggle debug overlay")
    print("  q      → quit")
    print("=" * 60)

    seq = PlacementSequencer(PCB_FILE, CAMERA_INDEX, SERIAL_PORT, verbose=VERBOSE)
    n   = seq.load_job()
    if n == 0:
        print("[ERROR] No components loaded — check PCB_FILE path")
        return

    seq.open_camera()
    seq.open_serial(BAUD_RATE)

    show_debug    = True
    last_result:  Optional[SequencerResult] = None
    live_comp                               = None   # last live detection for pad window

    DEBUG_WIN  = "Debug — body mask overlay"
    PAD_WIN    = "Pad detection (zoom)"
    THRESH_BAR = "Threshold (0=auto)"

    if show_debug:
        cv2.namedWindow(DEBUG_WIN)
        cv2.createTrackbar(THRESH_BAR, DEBUG_WIN, 0, 255, lambda _: None)
        cv2.namedWindow(PAD_WIN)

    while True:
        ret, frame = seq._cap.read() if (seq._cap and seq._cap.isOpened()) else (False, None)
        if not ret or frame is None:
            print("[ERROR] No frame — check camera index")
            break

        # Live detection every frame (no serial send) — drives the pad window
        try:
            thresh = cv2.getTrackbarPos(THRESH_BAR, DEBUG_WIN) if show_debug else 0
        except Exception:
            thresh = 0

        seq._pipeline.manual_thresh = thresh
        live_comp, live_mask = seq._pipeline.run(frame)

        des = seq.next_designator() or "(done)"
        cv2.imshow("Vision PnP — Placement Sequencer",
                   draw_result(frame, last_result, des))

        if show_debug:
            if live_mask is not None:
                cv2.imshow(DEBUG_WIN, draw_debug_overlay(frame, live_mask))
            cv2.imshow(PAD_WIN, draw_pad_debug(frame, seq._pipeline, live_comp))

        key = cv2.waitKey(1) & 0xFF

        if key == ord("q"):
            break

        elif key == ord("d"):
            show_debug = not show_debug
            if show_debug:
                cv2.namedWindow(DEBUG_WIN)
                cv2.createTrackbar(THRESH_BAR, DEBUG_WIN, 0, 255, lambda _: None)
                cv2.namedWindow(PAD_WIN)
            else:
                cv2.destroyWindow(DEBUG_WIN)
                cv2.destroyWindow(PAD_WIN)

        elif key == ord("r"):
            seq.reset()
            last_result = None
            print(f"[Nav] Reset → {seq.next_designator()}")

        elif key == ord("s"):
            seq.skip()
            last_result = None
            print(f"[Nav] Skipped → {seq.next_designator()}")

        elif key == ord(" "):
            if seq.next_designator() is None:
                print("[Info] Queue complete — press r to restart or q to quit")
                continue
            print(f"[PROC] Processing {seq.next_designator()} ...")
            last_result = seq.process_next(frame=frame)
            print(f"[PROC] {last_result.status.name}: {last_result.message}")
            print(f"[JSON] {last_result.json_payload.strip()}")

    seq.close()
    cv2.destroyAllWindows()
    print("Sequencer stopped.")


if __name__ == "__main__":
    main()
