"""Non-interactive detector debug runner.
Creates synthetic frames (LED, resistor) and runs `find_nozzle_component`
with debug logs enabled to capture classification metrics.
"""
import numpy as np
import cv2
import math

import orientation_engine as oe

# Enable classification debug
oe.DEBUG_CLASSIFY = True

CX = oe.FRAME_WIDTH // 2
CY = oe.FRAME_HEIGHT // 2


def _dark():
    return np.zeros((oe.FRAME_HEIGHT, oe.FRAME_WIDTH, 3), dtype=np.uint8)


def make_led(angle_deg: float = 0.0) -> np.ndarray:
    frame = _dark()
    a = math.radians(angle_deg)
    rot = np.array([[math.cos(a), -math.sin(a)], [math.sin(a), math.cos(a)]])

    # Off-white rectangular body
    body = np.array([[-30, -12], [30, -12], [30, 12], [-30, 12]], np.float32)
    cv2.fillPoly(frame, [(body @ rot.T).astype(int) + [CX, CY]], (210, 210, 200))

    # Dark cathode band on the right ~35% of the body
    band = np.array([[12, -12], [30, -12], [30, 12], [12, 12]], np.float32)
    cv2.fillPoly(frame, [(band @ rot.T).astype(int) + [CX, CY]], (65, 65, 60))

    # Two bright pads
    for dx in (-22, 22):
        rdx = int(dx * math.cos(a)); rdy = int(dx * math.sin(a))
        cv2.circle(frame, (CX + rdx, CY + rdy), 8, (255, 255, 200), -1)
        cv2.circle(frame, (CX + rdx, CY + rdy), 3, (10, 10, 10), -1)
    return frame


def make_resistance(angle_deg: float = 0.0) -> np.ndarray:
    frame = _dark()
    pts = np.array([[-90, -15], [90, -15], [90, 15], [-90, 15]], np.float32)
    a = math.radians(angle_deg)
    rot = np.array([[math.cos(a), -math.sin(a)], [math.sin(a), math.cos(a)]])
    pts = (pts @ rot.T).astype(int) + [CX, CY]
    cv2.fillPoly(frame, [pts], (200, 200, 190))
    for dx in (-75, 75):
        rdx = int(dx * math.cos(a)); rdy = int(dx * math.sin(a))
        cv2.circle(frame, (CX + rdx, CY + rdy), 10, (255, 255, 200), -1)
        cv2.circle(frame, (CX + rdx, CY + rdy), 4, (10, 10, 10), -1)
    return frame


def run_cases():
    cases = [
        ("LED 0", make_led, 0.0, "LED"),
        ("LED 30", make_led, 30.0, "LED"),
        ("R 0", make_resistance, 0.0, "RESISTANCE"),
    ]

    for label, fn, ang, expected in cases:
        frame = fn(ang)
        pipe = oe.VisionPipeline()
        comp, body_mask = pipe.run(frame)
        pad_mask = pipe._last_pad_mask
        print("\n---", label, f"(expected {expected})")

        # Dump pad mask for inspection
        try:
            cv2.imwrite(f"debug_{label.replace(' ', '_')}_padmask.png", pad_mask)
            cv2.imwrite(f"debug_{label.replace(' ', '_')}_frame.png", frame)
        except Exception:
            pass

        if comp is None:
            print("NOT DETECTED")
            continue
        print(f"Detected: {comp.comp_type} conf={comp.confidence:.2f} pads={comp.pad_count} method={comp.angle_method}")
        print(f"Angle={comp.angle:.1f} deg; pads={comp.pad_positions}")
        res = oe.check_orientation(comp, oe.PCBComponent("T1", expected, 0, 0, 0.0, "", "Top"))
        print(f"Orientation result: action={res.action} delta={res.delta_angle} message={res.message}")


if __name__ == '__main__':
    run_cases()
