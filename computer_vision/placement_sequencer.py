"""
placement_sequencer.py
======================
Orchestrates the full bottom-vision workflow during an automated pick-and-place run.
This is the production bridge between the detection engine and the machine hardware
(G-code sender + Arduino servo controller).

pnp_bottom_vision.py remains the manual debug/tuning tool — this module is what
gets called during an actual placement job.

Sequence for each component
----------------------------
1. Expect a specific designator (head of the queue, which mirrors centroid file order).
2. Wait for an external trigger (READY <des> on serial, or a direct process_next() call).
3. Capture one frame from the bottom camera.
4. Detect using VisionPipeline.
5. Verify geometry against the footprint envelope (WRONG_PART check).
6. check_orientation() computes rotation delta, respects 180° symmetry for passives.
7. Send JSON command to Arduino.
8. Wait up to ACK_TIMEOUT_S for "ACK <des> <PLACED|ROTATED|ERROR>".
9. Advance queue on PLACE; retry once on ROTATE_*; halt on REJECT / WRONG_PART.

Limitation: bottom-view alone cannot determine pin-1 reliably for symmetric SOIC
packages (e.g. 74xx logic, ATtiny, etc. where both halves look identical). The
sequencer warns when pin1_side=="unknown" for ICs but proceeds, trusting the
feeder orientation. Revisit when/if a top camera is added.

KiCad rotation convention: CCW-positive, degrees.  The engine preserves this
end-to-end; the Arduino receives the same sign convention.
"""

import cv2
import json
import time
import numpy as np
from dataclasses import dataclass
from enum import Enum, auto
from typing import Optional

from orientation_engine import (
    load_pcb,
    VisionPipeline,
    check_orientation,
    OrientationResult,
    PCBComponent,
    DetectedComponent,
    FRAME_WIDTH,
    FRAME_HEIGHT,
)


# ── Status enum ───────────────────────────────────────────────────────────────

class SequencerStatus(Enum):
    OK_PLACED      = auto()   # component placed successfully
    NEEDS_ROTATION = auto()   # rotation sent, waiting for re-verify
    WRONG_PART     = auto()   # geometry mismatch or REJECT — halt required
    NO_DETECTION   = auto()   # nothing visible in the frame
    RETRY          = auto()   # rotation ACK received; caller should re-capture


# ── Result dataclass ──────────────────────────────────────────────────────────

@dataclass
class SequencerResult:
    status:       SequencerStatus
    designator:   str
    orientation:  Optional[OrientationResult]
    json_payload: str            # the JSON line sent (or that would have been sent)
    message:      str


# ── Sequencer ─────────────────────────────────────────────────────────────────

class PlacementSequencer:
    """
    Drives the placement loop end-to-end.

    Offline / simulation mode
    -------------------------
    Pass camera_index=-1 to skip all camera calls (caller must supply frames
    directly to process_next()).  Pass serial_port=None (or omit) to simulate
    Arduino ACKs locally — every send is silently confirmed.
    """

    ACK_TIMEOUT_S = 5.0

    def __init__(self,
                 pcb_file: str,
                 camera_index: int = 0,
                 serial_port: Optional[str] = None,
                 verbose: bool = False):
        self.pcb_file     = pcb_file
        self.camera_index = camera_index
        self.serial_port  = serial_port
        self.verbose      = verbose

        self._pcb_data:  dict  = {}
        self._queue:     list  = []   # designators in file order
        self._head:      int   = 0    # index of next component
        self._pipeline         = VisionPipeline()
        self._cap              = None
        self._serial           = None
        self._retry_count: int = 0

    # ── Public API ─────────────────────────────────────────────────────────────

    def load_job(self) -> int:
        """
        Parse the PCB file and build the ordered placement queue.
        Returns the number of components in the queue.

        _load_csv uses DictReader which preserves row order (Python 3.7+ dict
        insertion order).  _load_kicad_pcb walks footprint blocks in document
        order so the queue mirrors the centroid file / schematic layout order.
        """
        self._pcb_data = load_pcb(self.pcb_file)
        self._queue    = list(self._pcb_data.keys())
        self._head     = 0
        if self.verbose:
            print(f"[Seq] Loaded {len(self._queue)} components: {self._queue}")
        return len(self._queue)

    def next_designator(self) -> Optional[str]:
        """Return the designator at the head of the queue, or None when done."""
        if self._head < len(self._queue):
            return self._queue[self._head]
        return None

    def process_next(self, frame: Optional[np.ndarray] = None) -> SequencerResult:
        """
        One full vision-check cycle for the component at the head of the queue.

        If `frame` is None and a camera is open, captures a live frame.
        Supply `frame` directly in offline/test mode.

        Returns SequencerResult.  Side-effects:
          - Sends JSON to Arduino on detection success.
          - Blocks for ACK (up to ACK_TIMEOUT_S).
          - Advances the queue on OK_PLACED.
          - Does NOT advance on WRONG_PART / NO_DETECTION (operator must skip).
        """
        des = self.next_designator()
        if des is None:
            return SequencerResult(
                status=SequencerStatus.OK_PLACED,
                designator="",
                orientation=None,
                json_payload="{}",
                message="Queue empty — job complete",
            )

        # ── Capture ────────────────────────────────────────────────────────────
        if frame is None:
            frame = self._capture_frame()
        if frame is None:
            return SequencerResult(
                status=SequencerStatus.NO_DETECTION,
                designator=des,
                orientation=None,
                json_payload=self._no_detect_json(des),
                message="Camera frame not available",
            )

        # ── Detect ─────────────────────────────────────────────────────────────
        detected, _ = self._pipeline.run(frame)
        if detected is None:
            if self.verbose:
                print(f"[Seq] No component detected for {des}")
            return SequencerResult(
                status=SequencerStatus.NO_DETECTION,
                designator=des,
                orientation=None,
                json_payload=self._no_detect_json(des),
                message="No component visible in frame",
            )

        # ── Footprint envelope check (catches gross wrong-part / empty nozzle) ─
        pcb_comp = self._pcb_data.get(des)
        footprint = pcb_comp.footprint if pcb_comp else ""
        consistent, reason = self._pipeline.is_consistent_with(detected, footprint)
        if not consistent:
            payload = self._wrong_part_json(des, detected, reason)
            if self.verbose:
                print(f"[Seq] WRONG_PART {des}: {reason}")
            return SequencerResult(
                status=SequencerStatus.WRONG_PART,
                designator=des,
                orientation=None,
                json_payload=payload,
                message=f"WRONG_PART: {reason}",
            )

        # ── Orientation check ──────────────────────────────────────────────────
        orientation = check_orientation(detected, pcb_comp)

        # Warn (but don't block) when pin-1 is ambiguous for a polarised IC
        if (detected.pad_count >= 6
                and detected.pin1_side == "unknown"
                and pcb_comp is not None
                and pcb_comp.comp_type == "IC"):
            if self.verbose:
                print(f"[Seq] WARNING: pin-1 unknown for {des} — trusting feeder orientation")

        # ── Build JSON and send ────────────────────────────────────────────────
        payload = self._build_json(des, orientation, detected.pin1_side)
        self._send_serial(payload)

        # ── Interpret result ───────────────────────────────────────────────────
        if orientation.action == "PLACE":
            self._wait_ack(des, "PLACED")
            self._head        += 1
            self._retry_count  = 0
            status = SequencerStatus.OK_PLACED
            msg    = f"Placed {des}"

        elif orientation.action in ("ROTATE_CW", "ROTATE_CCW"):
            self._wait_ack(des, "ROTATED")
            self._retry_count += 1
            status = SequencerStatus.NEEDS_ROTATION
            msg    = (f"{des}: {orientation.action} "
                      f"{abs(orientation.delta_angle):.1f}°  "
                      f"(retry #{self._retry_count})")

        else:  # REJECT from check_orientation (type mismatch or missing PCB entry)
            status = SequencerStatus.WRONG_PART
            msg    = f"{des}: REJECT — {orientation.message}"

        if self.verbose:
            print(f"[Seq] {status.name}: {msg}")

        return SequencerResult(
            status=status,
            designator=des,
            orientation=orientation,
            json_payload=payload,
            message=msg,
        )

    def reset(self):
        """Rewind the queue to the first component."""
        self._head        = 0
        self._retry_count = 0
        if self.verbose:
            print(f"[Seq] Queue reset — starting from {self.next_designator()}")

    def skip(self):
        """
        Operator-acknowledged skip: advance past the current head without
        placing.  Use after a WRONG_PART halt once the operator has resolved
        the feeder issue.
        """
        if self._head < len(self._queue):
            if self.verbose:
                print(f"[Seq] Skipping {self._queue[self._head]}")
            self._head        += 1
            self._retry_count  = 0

    # ── Camera helpers ─────────────────────────────────────────────────────────

    def open_camera(self):
        """Open the bottom camera.  No-op in simulation mode (camera_index < 0)."""
        if self.camera_index < 0:
            return
        self._cap = cv2.VideoCapture(self.camera_index, cv2.CAP_DSHOW)
        self._cap.set(cv2.CAP_PROP_FRAME_WIDTH,   FRAME_WIDTH)
        self._cap.set(cv2.CAP_PROP_FRAME_HEIGHT,  FRAME_HEIGHT)
        self._cap.set(cv2.CAP_PROP_AUTOFOCUS,     0)
        self._cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, 1)
        self._cap.set(cv2.CAP_PROP_EXPOSURE,     -6)
        self._cap.set(cv2.CAP_PROP_BRIGHTNESS,   150)
        if not self._cap.isOpened():
            print(f"[Seq] WARNING: cannot open camera {self.camera_index}")
            self._cap = None

    def open_serial(self, baud: int = 115200):
        """Open the Arduino serial port.  No-op when serial_port is None."""
        if self.serial_port is None:
            return
        try:
            import serial as _serial
            self._serial = _serial.Serial(self.serial_port, baud, timeout=1)
            time.sleep(2)
            if self.verbose:
                print(f"[Seq] Serial connected: {self.serial_port} @ {baud}")
        except Exception as e:
            print(f"[Seq] Serial unavailable ({e}) — simulation mode")
            self._serial = None

    def close(self):
        """Release camera and serial resources."""
        if self._cap and self._cap.isOpened():
            self._cap.release()
        if self._serial and self._serial.is_open:
            self._serial.close()

    def _capture_frame(self) -> Optional[np.ndarray]:
        if self._cap is None or not self._cap.isOpened():
            return None
        ret, frame = self._cap.read()
        return frame if ret else None

    # ── Serial helpers ─────────────────────────────────────────────────────────

    def _send_serial(self, payload: str):
        if self._serial and self._serial.is_open:
            try:
                self._serial.write(payload.encode("utf-8"))
            except Exception as e:
                if self.verbose:
                    print(f"[Seq] Serial send error: {e}")
        elif self.verbose:
            print(f"[SIM] → Arduino: {payload.strip()}")

    def _wait_ack(self, des: str, expected_word: str) -> bool:
        """
        Block up to ACK_TIMEOUT_S seconds for:
            ACK <des> <PLACED|ROTATED|ERROR>
        Returns True on success, False on timeout or ERROR.
        In simulation mode (no serial port) always returns True immediately.
        """
        if self._serial is None or not self._serial.is_open:
            if self.verbose:
                print(f"[SIM] ACK {des} {expected_word} (simulated)")
            return True

        deadline = time.monotonic() + self.ACK_TIMEOUT_S
        while time.monotonic() < deadline:
            try:
                if self._serial.in_waiting:
                    line = self._serial.readline().decode("utf-8").strip()
                    if self.verbose:
                        print(f"[Arduino] {line}")
                    parts = line.split()
                    if len(parts) >= 3 and parts[0] == "ACK" and parts[1] == des:
                        if parts[2] == "ERROR":
                            print(f"[Seq] Arduino ERROR for {des} — halting")
                            return False
                        return True
            except Exception:
                pass
            time.sleep(0.05)

        print(f"[Seq] ACK timeout waiting for {des} — halting")
        return False

    # ── JSON builders ──────────────────────────────────────────────────────────

    def _build_json(self, des: str, orientation: OrientationResult,
                    pin1: str) -> str:
        """
        Build the Arduino-bound JSON line.
        Format (single line, newline-terminated):
            {"des":"R1","ok":true,"type":"SMD_PASSIVE","delta":-12.3,
             "action":"ROTATE_CCW","conf":0.72,"method":"pads","pads":2,"pin1":"unknown"}
        """
        comp = orientation.component
        return json.dumps({
            "des":    des,
            "ok":     orientation.ok,
            "type":   comp.comp_type,
            "delta":  round(orientation.delta_angle, 1),
            "action": orientation.action,
            "conf":   round(comp.confidence, 2),
            "method": comp.angle_method,
            "pads":   comp.pad_count,
            "pin1":   pin1,
        }) + "\n"

    def _no_detect_json(self, des: str) -> str:
        return json.dumps({
            "des":    des,
            "ok":     False,
            "type":   "NONE",
            "delta":  0,
            "action": "REJECT",
            "conf":   0,
            "method": "none",
            "pads":   0,
            "pin1":   "unknown",
        }) + "\n"

    def _wrong_part_json(self, des: str, detected: DetectedComponent,
                          reason: str) -> str:
        return json.dumps({
            "des":    des,
            "ok":     False,
            "type":   detected.comp_type,
            "delta":  0,
            "action": "WRONG_PART",
            "conf":   round(detected.confidence, 2),
            "method": detected.angle_method,
            "pads":   detected.pad_count,
            "pin1":   "unknown",
            "reason": reason,
        }) + "\n"
