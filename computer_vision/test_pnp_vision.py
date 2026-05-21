"""
test_pnp_vision.py
==================
Tests the orientation engine WITHOUT a real camera or Arduino.
All component images are drawn synthetically.

Run:
    python test_pnp_vision.py

What it tests:
    1. Synthetic image generation
    2. Detection pipeline (mask, contours, Hough circles)
    3. Classification accuracy (expected vs detected type)
    4. Orientation logic (delta angle + symmetry handling)
    5. JSON output format
    6. KiCad .kicad_pcb loader (parses the flashing-led project)
    7. CSV loader (parses pcb_centroid.csv)

Press any key to advance, 'q' to abort early.
"""

import cv2
import numpy as np
import json
from pathlib import Path

from orientation_engine import (
    find_nozzle_component,
    check_orientation,
    preprocess,
    load_pcb,
    _smallest_delta,
    PCBComponent,
    DetectedComponent,
    VisionPipeline,
    FRAME_WIDTH, FRAME_HEIGHT, CENTER_RADIUS, ANGLE_TOLERANCE,
)

CX = FRAME_WIDTH  // 2
CY = FRAME_HEIGHT // 2


# ── Synthetic image generators ────────────────────────────────────────────────

def _dark() -> np.ndarray:
    return np.zeros((FRAME_HEIGHT, FRAME_WIDTH, 3), dtype=np.uint8)


def make_resistance(angle_deg: float = 0.0) -> np.ndarray:
    frame = _dark()
    pts   = np.array([[-90, -15], [90, -15], [90, 15], [-90, 15]], np.float32)
    a     = np.radians(angle_deg)
    rot   = np.array([[np.cos(a), -np.sin(a)], [np.sin(a), np.cos(a)]])
    pts   = (pts @ rot.T).astype(int) + [CX, CY]
    cv2.fillPoly(frame, [pts], (200, 200, 190))
    for dx in (-75, 75):
        rdx = int(dx * np.cos(a));  rdy = int(dx * np.sin(a))
        cv2.circle(frame, (CX + rdx, CY + rdy), 10, (255, 255, 200), -1)
        cv2.circle(frame, (CX + rdx, CY + rdy),  4, ( 10,  10,  10), -1)
    return frame


def make_led(angle_deg: float = 0.0) -> np.ndarray:
    """
    0805-style rectangular body with a dark cathode polarity band on the right
    end — the visual cue used by _led_or_passive to distinguish LED from R/C.
    """
    frame = _dark()
    a   = np.radians(angle_deg)
    rot = np.array([[np.cos(a), -np.sin(a)], [np.sin(a), np.cos(a)]])

    # Off-white rectangular body
    body = np.array([[-30, -12], [30, -12], [30, 12], [-30, 12]], np.float32)
    cv2.fillPoly(frame, [(body @ rot.T).astype(int) + [CX, CY]], (210, 210, 200))

    # Dark cathode band on the right ~35% of the body
    band = np.array([[12, -12], [30, -12], [30, 12], [12, 12]], np.float32)
    cv2.fillPoly(frame, [(band @ rot.T).astype(int) + [CX, CY]], (65, 65, 60))

    # Two bright pads
    for dx in (-22, 22):
        rdx = int(dx * np.cos(a));  rdy = int(dx * np.sin(a))
        cv2.circle(frame, (CX + rdx, CY + rdy), 8, (255, 255, 200), -1)
        cv2.circle(frame, (CX + rdx, CY + rdy), 3, ( 10,  10,  10), -1)
    return frame


def make_capacitor(angle_deg: float = 0.0) -> np.ndarray:
    frame = _dark()
    cv2.circle(frame, (CX, CY), 40, (160, 160, 180), -1)
    a = np.radians(angle_deg)
    for dx in (-25, 25):
        rdx = int(dx * np.cos(a));  rdy = int(dx * np.sin(a))
        cv2.circle(frame, (CX + rdx, CY + rdy), 9, (255, 255, 200), -1)
        cv2.circle(frame, (CX + rdx, CY + rdy), 3, ( 10,  10,  10), -1)
    return frame


def make_ic_dip(angle_deg: float = 0.0) -> np.ndarray:
    frame = _dark()
    pts   = np.array([[-50, -30], [50, -30], [50, 30], [-50, 30]], np.float32)
    a     = np.radians(angle_deg)
    rot   = np.array([[np.cos(a), -np.sin(a)], [np.sin(a), np.cos(a)]])
    pts   = (pts @ rot.T).astype(int) + [CX, CY]
    cv2.fillPoly(frame, [pts], (140, 130, 150))
    for dy in (-35, 35):
        for dx in (-45, -15, 15, 45):
            rdx = int(dx * np.cos(a) - dy * np.sin(a))
            rdy = int(dx * np.sin(a) + dy * np.cos(a))
            cv2.circle(frame, (CX + rdx, CY + rdy), 8, (255, 245, 180), -1)
            cv2.circle(frame, (CX + rdx, CY + rdy), 3, ( 10,  10,  10), -1)
    return frame


def make_smd(angle_deg: float = 0.0) -> np.ndarray:
    frame = _dark()
    pts   = np.array([[-25, -10], [25, -10], [25, 10], [-25, 10]], np.float32)
    a     = np.radians(angle_deg)
    rot   = np.array([[np.cos(a), -np.sin(a)], [np.sin(a), np.cos(a)]])
    pts   = (pts @ rot.T).astype(int) + [CX, CY]
    cv2.fillPoly(frame, [pts], (190, 180, 170))
    for dx in (-18, 18):
        rdx = int(dx * np.cos(a));  rdy = int(dx * np.sin(a))
        cv2.circle(frame, (CX + rdx, CY + rdy), 6, (240, 220, 180), -1)
    return frame


# ── Test cases ────────────────────────────────────────────────────────────────
# (label, image_fn, angle, expected_type, pcb_rotation, symmetric)
VISION_TESTS = [
    ("Resistance 0°",   make_resistance,  0.0, "RESISTANCE",   90.0, True),
    ("Resistance 45°",  make_resistance, 45.0, "RESISTANCE",   90.0, True),
    ("Resistance 90°",  make_resistance, 90.0, "RESISTANCE",   90.0, True),
    ("LED 0°",          make_led,         0.0, "LED",           0.0, False),
    ("LED 30° off",     make_led,        30.0, "LED",           0.0, False),
    ("Capacitor 0°",    make_capacitor,   0.0, "CONDENSATEUR",  0.0, True),
    ("IC DIP 0°",       make_ic_dip,      0.0, "IC",            0.0, False),
    ("IC DIP 90°",      make_ic_dip,     90.0, "IC",            0.0, False),
    ("SMD 0°",          make_smd,         0.0, "SMD_PASSIVE",   0.0, True),
]


# ── Helpers ───────────────────────────────────────────────────────────────────

def _validate_json(s: str) -> bool:
    try:
        d = json.loads(s.strip())
        return all(k in d for k in ("ok", "type", "delta", "action", "conf"))
    except Exception:
        return False


def _draw_result_simple(frame: np.ndarray,
                        comp, result, label: str) -> np.ndarray:
    out = frame.copy()
    if comp:
        cx, cy = comp.cx, comp.cy
        cv2.circle(out, (cx, cy), 8, (0, 255, 0), -1)
        ang = np.radians(comp.angle)
        cv2.arrowedLine(out, (cx, cy),
                        (int(cx + 60 * np.cos(ang)),
                         int(cy + 60 * np.sin(ang))),
                        (0, 255, 0), 2, tipLength=0.3)
    status = "PASS" if result else "—"
    cv2.putText(out, label, (10, 40),
                cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2)
    return out


# ── Vision test runner ────────────────────────────────────────────────────────

def run_vision_tests() -> tuple:
    passed = 0
    total  = len(VISION_TESTS)
    print(f"\n{'─'*60}")
    print("  Vision detection tests")
    print(f"{'─'*60}")

    for i, (label, img_fn, angle, expected_type, pcb_rot, symmetric) in enumerate(VISION_TESTS):
        frame = img_fn(angle)

        fake_pcb = PCBComponent(
            designator="T1", comp_type=expected_type,
            x_mm=0, y_mm=0, rotation=pcb_rot,
            footprint="", layer="Top",
        )

        comp, mask = find_nozzle_component(frame)
        result     = check_orientation(comp, fake_pcb) if comp else None

        _SMD = {"RESISTANCE", "CONDENSATEUR", "SMD_PASSIVE"}
        ok_type = bool(comp and (
            comp.comp_type == expected_type
            or (comp.comp_type == "SMD_PASSIVE" and expected_type in _SMD)
        ))

        ok_delta = False
        if result and comp:
            expected_delta = _smallest_delta(comp.angle, pcb_rot)
            if symmetric and abs(expected_delta) > 90:
                alt = expected_delta - 180 if expected_delta > 0 else expected_delta + 180
                if abs(alt) < abs(expected_delta):
                    expected_delta = alt
            ok_delta = abs(result.delta_angle - expected_delta) < 2.0

        ok_json    = _validate_json(result.to_serial_json()) if result else False
        test_pass  = ok_type and ok_delta and ok_json

        if test_pass:
            passed += 1

        status = "PASS ✓" if test_pass else "FAIL ✗"
        det    = comp.comp_type if comp else "NOT DETECTED"
        conf   = f"{comp.confidence*100:.0f}%" if comp else "—"
        method = comp.angle_method if comp else "—"
        act    = result.action if result else "—"
        dlt    = f"{result.delta_angle:+.1f}°" if result else "—"

        print(f"\n[{i+1:02d}/{total}] {status}  {label}")
        print(f"        Detected : {det} (conf={conf}, method={method})")
        print(f"        Action   : {act}  delta={dlt}")
        if result:
            print(f"        JSON     : {result.to_serial_json().strip()}")

        cv2.imshow("Test Frame", _draw_result_simple(frame, comp, result, label))
        thresh_vis, _ = preprocess(frame)
        cv2.imshow("Test Mask",  thresh_vis)
        print("        [any key = next  |  q = abort]")
        if cv2.waitKey(0) == ord("q"):
            break

    cv2.destroyAllWindows()
    return passed, total


# ── PCB loader tests ──────────────────────────────────────────────────────────

def run_loader_tests() -> tuple:
    passed = 0
    total  = 0
    print(f"\n{'─'*60}")
    print("  PCB loader tests")
    print(f"{'─'*60}")

    # CSV loader
    total += 1
    csv_path = "pcb_centroid.csv"
    if Path(csv_path).exists():
        pcb = load_pcb(csv_path)
        ok  = len(pcb) > 0 and "R1" in pcb and pcb["R1"].rotation == 90.0
        passed += int(ok)
        print(f"\n[CSV]  {'PASS ✓' if ok else 'FAIL ✗'}  "
              f"Loaded {len(pcb)} components — R1.rotation={pcb.get('R1', type('', (), {'rotation': '?'})()).rotation}")
    else:
        print(f"\n[CSV]  SKIP  '{csv_path}' not found")
        total -= 1

    # KiCad loader
    total += 1
    kicad_path = "flashing-led.kicad_pcb"
    if Path(kicad_path).exists():
        pcb = load_pcb(kicad_path)
        # Expect at least R2 (270°), C1 (90°), D1 (0°) from the flashing-led project
        ok = (len(pcb) > 0
              and "R2" in pcb and abs(pcb["R2"].rotation - 270.0) < 1.0
              and "C1" in pcb and abs(pcb["C1"].rotation -  90.0) < 1.0)
        passed += int(ok)
        print(f"\n[KICAD] {'PASS ✓' if ok else 'FAIL ✗'}  "
              f"Loaded {len(pcb)} components")
        for des, comp in sorted(pcb.items()):
            print(f"         {des:6s}  type={comp.comp_type:12s}  "
                  f"rot={comp.rotation:6.1f}°  fp={comp.footprint}")
    else:
        print(f"\n[KICAD] SKIP  '{kicad_path}' not found — "
              f"download from github.com/ItsKarlito/flashing-led")
        total -= 1

    return passed, total


# ── Sequencer tests (no camera, no Arduino) ───────────────────────────────────

def run_sequencer_tests() -> tuple:
    """
    Tests PlacementSequencer in offline / simulation mode.
    No camera or Arduino required — frames are supplied directly.

    Uses pcb_centroid.csv (R1/R2 → R_0805, C1 → C_0805, LED1 → LED_0805, U1 → SOIC-8).
    """
    from placement_sequencer import PlacementSequencer, SequencerStatus

    passed = 0
    total  = 0
    csv_path = "pcb_centroid.csv"

    print(f"\n{'─'*60}")
    print("  Sequencer tests (offline / simulation mode)")
    print(f"{'─'*60}")

    if not Path(csv_path).exists():
        print(f"\n  SKIP — '{csv_path}' not found")
        return 0, 0

    def _ok(label, cond):
        nonlocal passed, total
        total += 1
        passed += int(cond)
        print(f"\n[{total:02d}] {'PASS ✓' if cond else 'FAIL ✗'}  {label}")

    # ── 01: load_job returns non-zero ──────────────────────────────────────────
    seq = PlacementSequencer(csv_path, camera_index=-1, serial_port=None)
    n   = seq.load_job()
    _ok(f"load_job() returned {n} components", n > 0)

    # ── 02: next_designator matches first CSV row ──────────────────────────────
    first = seq.next_designator()
    _ok(f"next_designator()='{first}' (expected 'R1')", first == "R1")

    # ── 03: correct resistance frame → valid status, 'des' in JSON ────────────
    frame_r1  = make_resistance(0.0)
    result_r1 = seq.process_next(frame=frame_r1)
    des_in_json = False
    try:
        payload = json.loads(result_r1.json_payload)
        des_in_json = payload.get("des") == "R1"
    except Exception:
        pass
    status_ok = result_r1.status in (SequencerStatus.OK_PLACED,
                                     SequencerStatus.NEEDS_ROTATION)
    _ok(f"R1 correct part: status={result_r1.status.name}, des_in_json={des_in_json}",
        status_ok and des_in_json)

    # ── 04: queue advances on OK_PLACED, stays put on NEEDS_ROTATION ──────────
    if result_r1.status == SequencerStatus.OK_PLACED:
        advanced = seq.next_designator() != "R1"
    else:
        seq.skip()
        advanced = seq.next_designator() != "R1"
    _ok(f"Queue advanced past R1 → now at '{seq.next_designator()}'", advanced)

    # ── 05: WRONG_PART — IC frame supplied when R1 expected (type mismatch) ───
    seq2 = PlacementSequencer(csv_path, camera_index=-1, serial_port=None)
    seq2.load_job()
    assert seq2.next_designator() == "R1", "precondition"
    result_wp = seq2.process_next(frame=make_ic_dip(0.0))
    _ok(f"WRONG_PART for R1 + IC frame: status={result_wp.status.name}",
        result_wp.status == SequencerStatus.WRONG_PART)

    # ── 06: queue does NOT advance after WRONG_PART ────────────────────────────
    _ok(f"Queue held at R1 after WRONG_PART (got '{seq2.next_designator()}')",
        seq2.next_designator() == "R1")

    # ── 07: skip() advances past halted component ─────────────────────────────
    seq2.skip()
    _ok(f"skip() → now at '{seq2.next_designator()}' (expected 'R2')",
        seq2.next_designator() == "R2")

    # ── 08: reset() rewinds to R1 ─────────────────────────────────────────────
    seq2.reset()
    _ok(f"reset() → '{seq2.next_designator()}' (expected 'R1')",
        seq2.next_designator() == "R1")

    # ── 09: footprint-envelope API — 2-pad component vs SOIC-8 (expects 6-10) ──
    # Tests is_consistent_with directly with a known DetectedComponent so the
    # result doesn't depend on whether the synthetic image resolves pads.
    # (make_resistance embeds pads inside the body → 0 pads detected at runtime,
    # which is treated as "uncertain" — this tests the real mismatch path.)
    pipe_api = VisionPipeline()
    d_2pad   = DetectedComponent(
        "SMD_PASSIVE", CX, CY, 200, 50, 0.0, 0.75, 2, [], "pads")
    ok_soic, reason_soic = pipe_api.is_consistent_with(d_2pad, "SOIC-8")
    _ok(f"is_consistent_with SOIC-8 for 2-pad component fails: '{reason_soic}'",
        not ok_soic)

    # ── 10: 180° symmetry folding survives through sequencer for R/C ──────────
    # R1 has rotation=90°; resistance at 270° → delta should fold to ~0°
    seq4 = PlacementSequencer(csv_path, camera_index=-1, serial_port=None)
    seq4.load_job()
    result_sym = seq4.process_next(frame=make_resistance(270.0))
    delta_ok = False
    if result_sym.orientation:
        delta_ok = abs(result_sym.orientation.delta_angle) <= 90
    _ok(f"180° symmetry fold for R at 270° vs PCB=90°: "
        f"status={result_sym.status.name}, delta={result_sym.orientation.delta_angle if result_sym.orientation else '?'}",
        delta_ok)

    # ── 11: NO_DETECTION on blank frame ───────────────────────────────────────
    seq5 = PlacementSequencer(csv_path, camera_index=-1, serial_port=None)
    seq5.load_job()
    blank  = np.zeros((FRAME_HEIGHT, FRAME_WIDTH, 3), dtype=np.uint8)
    result_blank = seq5.process_next(frame=blank)
    _ok(f"NO_DETECTION on blank frame: {result_blank.status.name}",
        result_blank.status == SequencerStatus.NO_DETECTION)

    # ── 12: JSON payload has required keys ────────────────────────────────────
    seq6 = PlacementSequencer(csv_path, camera_index=-1, serial_port=None)
    seq6.load_job()
    result_j = seq6.process_next(frame=make_resistance(0.0))
    required_keys = {"des", "ok", "type", "delta", "action", "conf", "method", "pads", "pin1"}
    try:
        payload_d = json.loads(result_j.json_payload)
        keys_ok   = required_keys.issubset(payload_d.keys())
    except Exception:
        keys_ok = False
    _ok(f"JSON has all required keys ({required_keys}): {result_j.json_payload.strip()}",
        keys_ok)

    # ── 13: bottom-layer rotation mirroring ───────────────────────────────────
    fake_bottom = PCBComponent(
        designator="T_BOT", comp_type="SMD_PASSIVE",
        x_mm=0, y_mm=0, rotation=45.0,
        footprint="R_0805", layer="Bottom",
    )
    comp_b, _ = find_nozzle_component(make_resistance(0.0))
    if comp_b:
        res_b = check_orientation(comp_b, fake_bottom)
        # For Bottom layer with rotation=45°, required_angle = (-45) % 360 = 315°
        mirror_ok = abs(res_b.required_angle - 315.0) < 1.0
    else:
        mirror_ok = False
    _ok(f"Bottom-layer rotation mirror: required_angle={res_b.required_angle if comp_b else '?'}° (expected 315°)",
        mirror_ok)

    # ── 14: is_consistent_with — known footprint / matching detection ──────────
    pipe = VisionPipeline()
    comp_r, _ = find_nozzle_component(make_resistance(0.0))
    if comp_r:
        ok_env, reason_env = pipe.is_consistent_with(comp_r, "R_0805")
        _ok(f"is_consistent_with R_0805 for resistance frame: ok={ok_env} reason='{reason_env}'",
            ok_env)
    else:
        _ok("is_consistent_with R_0805 (detection failed — skipped)", False)

    # ── 15: is_consistent_with — IC detection vs R_0805 envelope (should fail) ─
    comp_ic, _ = find_nozzle_component(make_ic_dip(0.0))
    if comp_ic:
        ok_env2, reason_env2 = pipe.is_consistent_with(comp_ic, "R_0805")
        _ok(f"is_consistent_with R_0805 for IC frame should fail: ok={ok_env2} '{reason_env2}'",
            not ok_env2)
    else:
        _ok("is_consistent_with IC vs R_0805 (detection failed — skipped)", False)

    return passed, total


# ── Main ──────────────────────────────────────────────────────────────────────

def run_tests():
    print("\n" + "=" * 60)
    print("  Orientation Engine — Full Test Suite")
    print("=" * 60)

    v_pass, v_total = run_vision_tests()
    l_pass, l_total = run_loader_tests()
    s_pass, s_total = run_sequencer_tests()

    total_pass  = v_pass + l_pass + s_pass
    total_total = v_total + l_total + s_total

    print("\n" + "=" * 60)
    print(f"  Vision tests    : {v_pass}/{v_total}")
    print(f"  Loader tests    : {l_pass}/{l_total}")
    print(f"  Sequencer tests : {s_pass}/{s_total}")
    print(f"  Total           : {total_pass}/{total_total}")
    print("=" * 60)
    if total_pass == total_total:
        print("  All tests passed — ready for camera testing.")
    else:
        print("  Some tests failed — check output above.")
    print()


if __name__ == "__main__":
    run_tests()
