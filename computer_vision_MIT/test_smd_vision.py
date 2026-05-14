"""
test_smd_vision.py
==================
Quick live test — no PCB file needed, no CSV, no Arduino.
Hold a component bottom-up under the camera and watch the output.

Run:
    python test_smd_vision.py

Controls:
    q   quit
    s   save a snapshot of the current frame to disk (for debugging)
"""

import cv2
import numpy as np
from orientation_engine import find_nozzle_component, preprocess, VisionPipeline

# ── Config ────────────────────────────────────────────────────────────────────
CAMERA_INDEX   = 1      # change if wrong camera (try 0, 1, 2 with CAP_DSHOW)
MANUAL_THRESH  = 0      # 0 = auto Otsu; set a fixed value if auto is unstable
FRAME_WIDTH    = 1280
FRAME_HEIGHT   = 720

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


def draw(frame, comp, pad_mask):
    out = frame.copy()
    H, W = out.shape[:2]
    cx0, cy0 = W // 2, H // 2

    # Nozzle zone circle
    cv2.circle(out, (cx0, cy0), 280, (50, 50, 50), 1)
    cv2.line(out,  (cx0-30, cy0), (cx0+30, cy0), (50,50,50), 1)
    cv2.line(out,  (cx0, cy0-30), (cx0, cy0+30), (50,50,50), 1)

    if comp is None:
        cv2.putText(out, "Nothing detected — hold component bottom-up under camera",
                    (20, 40), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 80, 255), 2)
        return out

    color = _COLORS.get(comp.comp_type, (255, 255, 255))
    bx = comp.cx - comp.width  // 2
    by = comp.cy - comp.height // 2
    cv2.rectangle(out, (bx, by), (bx+comp.width, by+comp.height), color, 2)
    cv2.circle(out, (comp.cx, comp.cy), 4, color, -1)

    # Orientation arrow
    ang = np.radians(comp.angle)
    cv2.arrowedLine(out,
                    (comp.cx, comp.cy),
                    (int(comp.cx + 80*np.cos(ang)),
                     int(comp.cy + 80*np.sin(ang))),
                    color, 2, tipLength=0.25)

    # Pad markers
    for px, py in comp.pad_positions:
        cv2.circle(out, (px, py), 5, (0, 255, 255), 2)

    # Info panel
    lines = [
        f"Type   : {comp.comp_type}",
        f"Pads   : {comp.pad_count}",
        f"Angle  : {comp.angle:.1f} deg",
        f"Method : {comp.angle_method}",
        f"Conf   : {comp.confidence:.0%}",
        f"Size   : {comp.width}x{comp.height} px",
    ]
    for i, ln in enumerate(lines):
        cv2.putText(out, ln, (12, 30 + i*26),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (220,220,220), 1)

    return out


def pad_overlay(frame, pad_mask):
    """Show detected pad blobs highlighted in cyan on the original frame."""
    overlay = frame.copy()
    overlay[pad_mask > 0] = (
        overlay[pad_mask > 0] * 0.3 +
        np.array([255, 255, 0]) * 0.7
    ).astype(np.uint8)
    return overlay


def main():
    print("=" * 50)
    print("  SMD Vision — Live Test")
    print("=" * 50)
    print(f"  Camera index : {CAMERA_INDEX}")
    print(f"  Threshold    : {'auto' if MANUAL_THRESH == 0 else MANUAL_THRESH}")
    print()
    print("  Hold a component BOTTOM-UP under the camera")
    print("  Printed info:")
    print("    Type   — what the system thinks it is")
    print("    Pads   — number of solder pads detected")
    print("    Angle  — current orientation (degrees)")
    print("    Method — how angle was computed")
    print()
    print("  If Pads=0 → camera too far, bad lighting, or threshold wrong")
    print("  If Type=INCONNU → component too small or out of focus")
    print()
    print("  Keys: q=quit  s=save snapshot")
    print("=" * 50)

    cap = cv2.VideoCapture(CAMERA_INDEX, cv2.CAP_DSHOW)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH,  FRAME_WIDTH)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, FRAME_HEIGHT)

    if not cap.isOpened():
        print(f"[ERROR] Cannot open camera {CAMERA_INDEX}")
        print("        Try CAMERA_INDEX = 0 or 2")
        return

    snap = 0
    pipeline = VisionPipeline(MANUAL_THRESH)

    while True:
        ret, frame = cap.read()
        if not ret:
            print("[ERROR] No frame"); break

        # Run full pipeline
        blur, v_eq = pipeline._preprocess(frame)
        pad_mask   = pipeline._pad_mask(blur)
        body_mask  = pipeline._body_mask(blur)
        comp, _    = pipeline.run(frame)

        # Draw main view
        annotated = draw(frame, comp, pad_mask)
        cv2.imshow("SMD Vision — main", annotated)

        # Pad overlay (cyan = bright pads found)
        cv2.imshow("Pad mask (cyan=pads)", pad_overlay(frame, pad_mask))

        # Body mask (white = component body)
        cv2.imshow("Body mask", body_mask)

        # Terminal output
        if comp:
            print(f"  {comp.comp_type:12s}  pads={comp.pad_count}  "
                  f"angle={comp.angle:6.1f}°  method={comp.angle_method:10s}  "
                  f"conf={comp.confidence:.0%}  size={comp.width}x{comp.height}px")

        key = cv2.waitKey(1) & 0xFF
        if key == ord("q"):
            break
        elif key == ord("s"):
            fname = f"snapshot_{snap:03d}.png"
            cv2.imwrite(fname, frame)
            print(f"  [Saved] {fname}")
            snap += 1

    cap.release()
    cv2.destroyAllWindows()
    print("Done.")


if __name__ == "__main__":
    main()
