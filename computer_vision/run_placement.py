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


# ── Combined debug view ───────────────────────────────────────────────────────
#
# Single 1280×720 canvas layout:
#
#  ┌─────────────────────┬────────────────┬────────────────┐  ← row 0, h=410
#  │  Camera + result    │   Pad zoom     │   Body mask    │
#  │  (640×410)          │   (320×410)    │   (320×410)    │
#  ├──────────┬──────────┼──────────┬─────┴────────────────┤  ← row 1, h=310
#  │  Color   │  Shape   │ Thresh.  │  Decision tree       │
#  │  (320×310)│(320×310)│  (320×310)│  (320×310)          │
#  └──────────┴──────────┴──────────┴─────────────────────-┘
#
# Press 'd' to toggle bottom debug row on/off.

_FONT  = cv2.FONT_HERSHEY_SIMPLEX
_SMALL = 0.40
_MED   = 0.50
_DIVIDER_COLOR = (45, 45, 45)


def _panel(h: int, w: int, title: str, title_color=(180, 180, 180)) -> np.ndarray:
    p = np.full((h, w, 3), 22, dtype=np.uint8)
    cv2.rectangle(p, (0, 0), (w - 1, h - 1), (55, 55, 55), 1)
    cv2.putText(p, title, (6, 16), _FONT, _SMALL, title_color, 1)
    cv2.line(p, (0, 20), (w, 20), (55, 55, 55), 1)
    return p


def _bar(panel, y, x0, x1, value, vmin, vmax, threshold, label,
         bar_color=(100, 200, 100), thresh_color=(0, 100, 255)):
    """Draw a labelled horizontal progress bar with a threshold marker."""
    BAR_H = 12
    frac  = max(0.0, min(1.0, (value - vmin) / max(vmax - vmin, 1e-6)))
    tfrac = max(0.0, min(1.0, (threshold - vmin) / max(vmax - vmin, 1e-6)))
    bar_w = x1 - x0
    cv2.rectangle(panel, (x0, y), (x1, y + BAR_H), (50, 50, 50), -1)
    cv2.rectangle(panel, (x0, y), (x0 + int(frac * bar_w), y + BAR_H), bar_color, -1)
    tx = x0 + int(tfrac * bar_w)
    cv2.line(panel, (tx, y - 2), (tx, y + BAR_H + 2), thresh_color, 2)
    cv2.putText(panel, f"{label}: {value:.3f}  [>{threshold}]" if value >= threshold
                else f"{label}: {value:.3f}  [<{threshold}]",
                (x0, y - 3), _FONT, _SMALL - 0.02, (200, 200, 200), 1)


def _tile_pad_zoom(frame: np.ndarray, pipeline, comp,
                   out_w: int, out_h: int) -> np.ndarray:
    pad_mask = getattr(pipeline, '_last_pad_mask', None)
    if comp is None or pad_mask is None:
        t = _panel(out_h, out_w, "PAD ZOOM")
        cv2.putText(t, "No component", (8, out_h // 2), _FONT, _MED, (70, 70, 70), 1)
        return t

    raw_half = max(comp.width, comp.height) // 2 + 50
    ZOOM = max(1, min(out_w, out_h) // (raw_half * 2))
    HALF = min(out_w, out_h) // (2 * ZOOM)

    H, W = frame.shape[:2]
    cx, cy = comp.cx, comp.cy
    x1 = max(0, cx - HALF); y1 = max(0, cy - HALF)
    x2 = min(W, cx + HALF); y2 = min(H, cy + HALF)

    gray_crop = cv2.cvtColor(frame[y1:y2, x1:x2], cv2.COLOR_BGR2GRAY)
    base = cv2.cvtColor(gray_crop, cv2.COLOR_GRAY2BGR)
    pm_crop = pad_mask[y1:y2, x1:x2]
    hot = pm_crop > 0
    base[hot] = (base[hot] * 0.35 + np.array([200, 80, 0]) * 0.65).astype(np.uint8)

    SZ = min(out_w, out_h)
    interp = cv2.INTER_NEAREST if ZOOM >= 2 else cv2.INTER_LINEAR
    out = cv2.resize(base, (SZ, SZ), interpolation=interp)
    if out_w != out_h:
        canvas = np.zeros((out_h, out_w, 3), dtype=np.uint8)
        ox = (out_w - SZ) // 2
        canvas[:SZ, ox:ox + SZ] = out
        out = canvas

    ocx = int((cx - x1) * ZOOM) + (out_w - SZ) // 2
    ocy = int((cy - y1) * ZOOM)
    cv2.circle(out, (ocx, ocy), 4, (0, 140, 255), -1)
    ang = np.radians(comp.angle)
    arr = max(25, int(45 * ZOOM / 4))
    cv2.arrowedLine(out, (ocx, ocy),
                    (int(ocx + arr * np.cos(ang)), int(ocy + arr * np.sin(ang))),
                    (0, 220, 220), 2, tipLength=0.25)
    pad_r = max(4, int(9 * ZOOM / 4))
    for i, (px, py) in enumerate(comp.pad_positions):
        ppx = int((px - x1) * ZOOM) + (out_w - SZ) // 2
        ppy = int((py - y1) * ZOOM)
        if 0 <= ppx < out_w and 0 <= ppy < out_h:
            cv2.circle(out, (ppx, ppy), pad_r, (0, 255, 0), 2)
            cv2.putText(out, f"P{i+1}", (ppx + pad_r, ppy - 3),
                        _FONT, 0.35, (0, 255, 0), 1)

    color_hint  = getattr(pipeline, '_last_color_hint', '?')
    clabel = {"blue_body": "BLUE=cap", "teal_marker": "TEAL=led",
              "dark_body": "DARK=res"}.get(color_hint, color_hint)
    mcol = (0, 220, 220) if "led" in comp.angle_method else (200, 200, 200)
    cv2.rectangle(out, (0, 0), (out_w, 20), (20, 20, 20), -1)
    cv2.putText(out, f"PAD ZOOM  {len(comp.pad_positions)}pads | {comp.angle:.1f}° | {clabel}",
                (4, 14), _FONT, _SMALL, mcol, 1)
    if comp.angle_method in ("pca", "bbox"):
        cv2.putText(out, "NO PADS", (4, 32), _FONT, _SMALL, (0, 60, 255), 1)
    cv2.putText(out, f"zoom {ZOOM}x", (4, out_h - 5), _FONT, _SMALL - 0.05, (60, 60, 60), 1)
    return out


def _tile_body_mask(frame: np.ndarray, live_mask, out_w: int, out_h: int) -> np.ndarray:
    p = _panel(out_h, out_w, "BODY MASK  (green = detected body)")
    if live_mask is None:
        cv2.putText(p, "No mask", (8, out_h // 2), _FONT, _MED, (70, 70, 70), 1)
        return p
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    base = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
    gm = live_mask > 0
    base[gm] = (base[gm] * 0.4 + np.array([0, 200, 60]) * 0.6).astype(np.uint8)
    scaled = cv2.resize(base, (out_w, out_h - 22))
    p[22:, :] = scaled
    coverage = np.count_nonzero(live_mask) / live_mask.size
    col = (0, 60, 255) if coverage > 0.55 else (0, 200, 60)
    cv2.putText(p, f"coverage {coverage*100:.1f}%", (out_w - 130, 15), _FONT, _SMALL, col, 1)
    return p


def _tile_color_analysis(pipeline, out_w: int, out_h: int) -> np.ndarray:
    p = _panel(out_h, out_w, "COLOR ANALYSIS", (180, 140, 220))
    info = getattr(pipeline, '_last_debug_info', {})
    hues = info.get('hue_values', None)
    blue_r = info.get('blue_ratio', 0.0)
    green_r = info.get('green_ratio', 0.0)
    hint = getattr(pipeline, '_last_color_hint', 'unknown')

    HIST_Y, HIST_H, HIST_W = 30, 80, out_w - 20
    # Background colour bands
    for h_start, h_end, col in [(0, 50, (40, 30, 30)), (50, 100, (20, 45, 20)),
                                  (100, 130, (20, 30, 55)), (130, 180, (35, 20, 20))]:
        x0 = 10 + int(h_start / 180 * HIST_W)
        x1 = 10 + int(h_end   / 180 * HIST_W)
        cv2.rectangle(p, (x0, HIST_Y), (x1, HIST_Y + HIST_H), col, -1)

    if hues is not None and len(hues) > 0:
        hist, _ = np.histogram(hues, bins=180, range=(0, 180))
        hist_max = max(hist.max(), 1)
        for i, cnt in enumerate(hist):
            bh = int(cnt / hist_max * HIST_H)
            bx = 10 + int(i / 180 * HIST_W)
            if bh > 0:
                cv2.line(p, (bx, HIST_Y + HIST_H), (bx, HIST_Y + HIST_H - bh), (220, 220, 80), 1)

    # Threshold markers
    for hv, lbl, col in [(50, "50", (60, 160, 60)), (100, "100", (80, 100, 200)),
                          (130, "130", (80, 60, 200))]:
        tx = 10 + int(hv / 180 * HIST_W)
        cv2.line(p, (tx, HIST_Y - 4), (tx, HIST_Y + HIST_H + 4), col, 1)
        cv2.putText(p, lbl, (tx - 6, HIST_Y - 6), _FONT, 0.28, col, 1)

    cv2.putText(p, "0", (10, HIST_Y + HIST_H + 14), _FONT, _SMALL - 0.05, (100, 100, 100), 1)
    cv2.putText(p, "179", (out_w - 28, HIST_Y + HIST_H + 14), _FONT, _SMALL - 0.05, (100, 100, 100), 1)

    y = HIST_Y + HIST_H + 28
    HINT_COLORS = {'blue_body': (200, 100, 0), 'teal_marker': (0, 200, 100),
                   'dark_body': (80, 200, 80), 'unknown': (100, 100, 100)}
    hint_col = HINT_COLORS.get(hint, (180, 180, 180))
    cv2.putText(p, f"blue_ratio : {blue_r:.3f}  (thresh >0.25)", (8, y),
                _FONT, _SMALL, (100, 160, 220) if blue_r > 0.25 else (160, 160, 160), 1)
    y += 18
    cv2.putText(p, f"green_ratio: {green_r:.3f}  (thresh >0.01)", (8, y),
                _FONT, _SMALL, (80, 200, 80) if green_r > 0.01 else (160, 160, 160), 1)
    y += 22
    hint_labels = {'blue_body': 'BLUE → CONDENSATEUR',
                   'teal_marker': 'TEAL → LED candidate',
                   'dark_body':   'DARK → resistor/cap'}
    cv2.rectangle(p, (6, y - 14), (out_w - 6, y + 6), hint_col, -1)
    cv2.putText(p, hint_labels.get(hint, hint), (10, y), _FONT, _SMALL, (0, 0, 0), 1)

    y += 26
    cv2.putText(p, "Bands:  0-49 dark | 50-99 green | 100-130 blue | 131+ red",
                (6, y), _FONT, 0.32, (100, 100, 100), 1)
    return p


def _tile_shape(pipeline, out_w: int, out_h: int) -> np.ndarray:
    p = _panel(out_h, out_w, "BODY SHAPE", (180, 200, 140))
    info = getattr(pipeline, '_last_debug_info', {})
    circ  = info.get('circularity', 0.0)
    ratio = info.get('ratio', 1.0)
    depth = info.get('depth_ratio', None)
    area_r = info.get('area_ratio', None)

    X0, X1 = 10, out_w - 10
    y = 32
    _bar(p, y, X0, X1, circ,  0.0, 1.0, 0.80, "circularity",
         bar_color=(100, 200, 200), thresh_color=(0, 100, 255))
    y += 38
    _bar(p, y, X0, X1, min(ratio, 5.0), 1.0, 5.0, 1.5, "aspect ratio",
         bar_color=(150, 200, 100), thresh_color=(0, 180, 255))
    y += 38

    if depth is not None:
        _bar(p, y, X0, X1, depth, 0.0, 1.0, 0.68, "depth_ratio",
             bar_color=(180, 120, 200), thresh_color=(255, 80, 80))
        cv2.putText(p, "  <0.68 → cap  |  >=0.68 → res", (X0, y + 26),
                    _FONT, 0.33, (150, 150, 150), 1)
        y += 44
    else:
        cv2.putText(p, "depth_ratio: N/A (needs 2 pads)", (X0, y + 12),
                    _FONT, _SMALL, (80, 80, 80), 1)
        y += 28

    if area_r is not None:
        _bar(p, y, X0, X1, min(area_r, 0.20), 0.0, 0.20, 0.08, "area_ratio",
             bar_color=(200, 160, 80), thresh_color=(255, 80, 80))
        cv2.putText(p, "  >0.08 → cap", (X0, y + 26), _FONT, 0.33, (150, 150, 150), 1)
        y += 44
    else:
        cv2.putText(p, "area_ratio: N/A (needs 2 pads)", (X0, y + 12),
                    _FONT, _SMALL, (80, 80, 80), 1)
        y += 28

    cv2.putText(p, f"body_area: {info.get('body_area', 0):.0f} px²",
                (X0, y + 12), _FONT, _SMALL, (160, 160, 160), 1)
    return p


def _tile_thresholds(pipeline, out_w: int, out_h: int) -> np.ndarray:
    p = _panel(out_h, out_w, "PAD THRESHOLDS", (220, 180, 100))
    info = getattr(pipeline, '_last_debug_info', {})
    body_hi  = info.get('body_hi',   0)
    t_lo     = info.get('thresh_lo', 0)
    t_hi     = info.get('thresh_hi', 255)
    bg_low   = info.get('bg_low',    0)
    bright   = info.get('bright_bg', False)

    # Brightness scale bar (0-255, vertical, centered)
    BAR_X, BAR_W = out_w // 2 - 12, 24
    BAR_Y, BAR_H = 30, out_h - 80
    # Gradient background
    for i in range(BAR_H):
        v = int(255 * (1 - i / BAR_H))
        cv2.line(p, (BAR_X, BAR_Y + i), (BAR_X + BAR_W, BAR_Y + i), (v, v, v), 1)
    cv2.rectangle(p, (BAR_X, BAR_Y), (BAR_X + BAR_W, BAR_Y + BAR_H), (80, 80, 80), 1)

    def _marker(val, col, label, side='left'):
        vy = BAR_Y + int((1 - val / 255) * BAR_H)
        x0 = BAR_X - 4 if side == 'left' else BAR_X + BAR_W + 4
        cv2.line(p, (BAR_X - 6, vy), (BAR_X + BAR_W + 6, vy), col, 2)
        tx = 4 if side == 'left' else BAR_X + BAR_W + 8
        cv2.putText(p, f"{label}={val}", (tx, vy + 4), _FONT, _SMALL, col, 1)

    _marker(body_hi, (160, 160, 160), "body_hi",  'left')
    _marker(t_lo,    (0, 220, 100),   "lo",       'left')
    if bright:
        _marker(t_hi, (0, 100, 255), "hi",        'right')
        _marker(bg_low, (0, 200, 255), "bg",       'right')
    else:
        cv2.putText(p, "hi=255", (BAR_X + BAR_W + 8, 50), _FONT, _SMALL, (80, 80, 80), 1)

    # Shade the active detection range
    lo_y = BAR_Y + int((1 - t_lo / 255) * BAR_H)
    hi_y = BAR_Y + int((1 - min(t_hi, 255) / 255) * BAR_H)
    cv2.rectangle(p, (BAR_X + 2, hi_y), (BAR_X + BAR_W - 2, lo_y),
                  (0, 100, 30), -1)

    mode = "inRange (bright bg)" if bright else "threshold (dark bg)"
    cv2.putText(p, mode, (4, out_h - 32), _FONT, _SMALL, (180, 180, 180), 1)
    cv2.putText(p, f"nb_pads: {info.get('nb_pads', '?')}",
                (4, out_h - 16), _FONT, _SMALL, (200, 200, 200), 1)
    return p


def _tile_decision(pipeline, comp, out_w: int, out_h: int) -> np.ndarray:
    p = _panel(out_h, out_w, "DECISION TREE", (220, 140, 100))
    if comp is None:
        cv2.putText(p, "No component detected", (8, out_h // 2), _FONT, _MED, (70, 70, 70), 1)
        return p

    info   = getattr(pipeline, '_last_debug_info', {})
    hint   = getattr(pipeline, '_last_color_hint', '?')
    nb     = info.get('nb_pads', 0)
    circ   = info.get('circularity', 0.0)
    ratio  = info.get('ratio', 1.0)
    depth  = info.get('depth_ratio', None)
    area_r = info.get('area_ratio', None)
    result = info.get('comp_type', comp.comp_type)
    conf   = info.get('confidence', comp.confidence)

    ACTIVE = (0, 230, 100)
    SKIP   = (60, 60, 60)
    FAIL   = (80, 80, 80)
    MATCH  = _COLORS.get(result, (200, 200, 200))

    def row(y, condition_true: bool, text: str, outcome: str = ""):
        col = ACTIVE if condition_true else SKIP
        prefix = ">" if condition_true else " "
        cv2.putText(p, f"{prefix} {text}", (6, y), _FONT, _SMALL, col, 1)
        if outcome and condition_true:
            cv2.putText(p, outcome, (out_w - len(outcome)*7 - 4, y), _FONT, _SMALL, MATCH, 1)

    y = 30
    row(y, nb >= 14,  f"pads>=14 → IC");                    y += 17
    row(y, 6<=nb<14,  f"pads>=6  → IC");                    y += 17
    row(y, 4<=nb<6,   f"pads>=4  → CONNECTOR");             y += 17
    row(y, nb == 2,   f"pads=2 ({nb})");                    y += 17
    if nb == 2:
        row(y, circ > 0.80,  f"  circ={circ:.2f} >0.80  → ELCAP");   y += 17
        row(y, hint=='blue_body', f"  color=blue           → CAP");    y += 17
        row(y, hint in ('teal_marker',),
            f"  color=teal           → LED");                           y += 17
        if depth is not None:
            row(y, depth < 0.68, f"  depth={depth:.2f} <0.68 → CAP"); y += 17
            row(y, depth >= 0.68,f"  depth={depth:.2f} >=0.68→ RES"); y += 17
        if area_r is not None:
            row(y, area_r > 0.08,f"  area_r={area_r:.3f}>0.08→ CAP");  y += 17
        row(y, hint=='dark_body' and depth is None,
            f"  dark_body fallback   → RES");                           y += 17
    else:
        row(y, nb <= 1,    f"pads<=1  (no pad resolve)");   y += 17
        row(y, hint=='blue_body',    f"  color=blue  → CAP");   y += 17
        row(y, hint=='teal_marker',  f"  color=teal  → LED");   y += 17
        row(y, circ > 0.80,          f"  circ={circ:.2f} >0.80→ ELCAP"); y += 17
        row(y, hint=='dark_body' and ratio > 1.2,
            f"  dark+ratio={ratio:.2f}>1.2→ RES");                    y += 17
        row(y, ratio > 1.5 and hint != 'dark_body',
            f"  ratio={ratio:.2f} >1.5  → SMD_PASSIVE");              y += 17

    y = out_h - 36
    cv2.rectangle(p, (4, y - 16), (out_w - 4, out_h - 4), MATCH, 2)
    cv2.putText(p, f"  {result}  ({conf*100:.0f}%)", (10, y + 3),
                _FONT, _MED, MATCH, 2)
    return p


def build_combined_view(frame: np.ndarray, pipeline, comp,
                        live_mask, last_result, designator: str,
                        show_debug: bool) -> np.ndarray:
    W, H = 1280, 720
    TOP  = 410   # camera + pad zoom + body mask row height
    BOT  = H - TOP  # 310 — analysis panels row height

    # ── Top row ───────────────────────────────────────────────────────────────
    # Camera tile (640×TOP)
    cam_tile = draw_result(frame, last_result, designator)
    cam_tile = cv2.resize(cam_tile, (640, TOP))

    if show_debug:
        pad_tile  = _tile_pad_zoom(frame, pipeline, comp, 320, TOP)
        mask_tile = _tile_body_mask(frame, live_mask, 320, TOP)
        top_row   = np.hstack([cam_tile, pad_tile, mask_tile])

        # ── Bottom row ────────────────────────────────────────────────────────
        c_tile = _tile_color_analysis(pipeline, 320, BOT)
        s_tile = _tile_shape(pipeline, 320, BOT)
        t_tile = _tile_thresholds(pipeline, 320, BOT)
        d_tile = _tile_decision(pipeline, comp, 320, BOT)
        bot_row = np.hstack([c_tile, s_tile, t_tile, d_tile])

        canvas = np.vstack([top_row, bot_row])
    else:
        canvas = np.hstack([cam_tile,
                            np.zeros((TOP, W - 640, 3), dtype=np.uint8)])
        canvas = np.vstack([canvas,
                            np.zeros((BOT, W, 3), dtype=np.uint8)])

    cv2.putText(canvas, "d=debug  SPACE=process  r=reset  s=skip  q=quit",
                (4, H - 6), _FONT, 0.34, (60, 60, 60), 1)
    return canvas


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
    print("  d      → toggle debug panels")
    print("  q      → quit")
    print("=" * 60)

    seq = PlacementSequencer(PCB_FILE, CAMERA_INDEX, SERIAL_PORT, verbose=VERBOSE)
    n   = seq.load_job()
    if n == 0:
        print("[ERROR] No components loaded — check PCB_FILE path")
        return

    seq.open_camera()
    seq.open_serial(BAUD_RATE)

    show_debug   = True
    last_result: Optional[SequencerResult] = None

    WIN       = "Vision PnP"
    THRESH_BAR = "Threshold (0=auto)"
    cv2.namedWindow(WIN)
    cv2.createTrackbar(THRESH_BAR, WIN, 0, 255, lambda _: None)

    while True:
        ret, frame = seq._cap.read() if (seq._cap and seq._cap.isOpened()) else (False, None)
        if not ret or frame is None:
            print("[ERROR] No frame — check camera index")
            break

        try:
            thresh = cv2.getTrackbarPos(THRESH_BAR, WIN)
        except Exception:
            thresh = 0

        seq._pipeline.manual_thresh = thresh
        live_comp, live_mask = seq._pipeline.run(frame)

        des = seq.next_designator() or "(done)"
        cv2.imshow(WIN, build_combined_view(frame, seq._pipeline, live_comp,
                                            live_mask, last_result, des, show_debug))

        key = cv2.waitKey(1) & 0xFF

        if key == ord("q"):
            break
        elif key == ord("d"):
            show_debug = not show_debug
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
