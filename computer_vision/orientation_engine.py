"""
orientation_engine.py  —  SMD edition
======================================
Rewritten for surface-mount components only.

Component list handled:
    SOIC-8   (NE555DR)
    SOIC-16  (CD4017BM96)
    0805 LED (VLMS1300)
    0805 Resistor (220R, 10k, 100k, 270k, 470k)
    0805 Capacitor (10nF, 10uF)
    SMD Electrolytic cap (EEE-FK1H010P)
    USB Micro-B connector (5-pin SMD)

Key differences from through-hole version
------------------------------------------
* No Hough circles  — SMD has no holes, replaced by pad blob detection
* Orientation       — axis between the two pad centroids (most reliable for 0805)
* Classification    — by pad count + body shape + cathode-band brightness:
      2 pads, elongated, asymmetric brightness → LED   (cathode polarity band)
      2 pads, elongated, symmetric brightness  → SMD_PASSIVE (R or C, use CSV)
      2 pads, round                            → Electrolytic cap
      8 pads, 2 rows                           → SOIC-8
      16 pads, 2 rows                          → SOIC-16
      5+ pads, asymmetric                      → Connector
* LED detection: 0805 LEDs have a dark cathode band on one end of the body
  visible from below.  Brightness asymmetry between left/right body halves
  (threshold: LED_BODY_ASYM_THRESH) distinguishes them from resistors/caps.
  Resistors and capacitors remain indistinguishable → labelled SMD_PASSIVE.

IMPORTANT — camera distance
---------------------------
An 0805 component is 2 mm × 1.25 mm.
At 720p with a standard wide-angle USB lens, you typically need the camera
5–15 cm from the component for the pads to be resolvable.
Run camera_diagnostic.py first to verify the component fills a reasonable
area of the frame (at least 20×12 pixels = 240 px²).
"""

import cv2
import numpy as np
import csv
import re
import json
from dataclasses import dataclass
from typing import Optional
from pathlib import Path

# ── Tuneable parameters ───────────────────────────────────────────────────────
FRAME_WIDTH     = 1280
FRAME_HEIGHT    = 720
CENTER_RADIUS   = 280     # px — nozzle search zone radius
ANGLE_TOLERANCE = 5.0     # degrees — delta ≤ this → PLACE

# Pad blob detector (replaces Hough circles)
PAD_MIN_AREA    = 20      # px² — ignore tiny noise blobs (ring light reflections)
PAD_MAX_AREA    = 4000    # px² — ignore large body reflections

# Body contour size limits
BODY_MIN_AREA   = 80      # px² — smallest valid 0805 at close range
BODY_MAX_AREA   = 200_000 # px²

# LED cathode-band detector
LED_BODY_ASYM_THRESH = 5   # intensity units — min left/right brightness difference to flag LED

# Enable to print detailed per-component classification metrics for debugging
DEBUG_CLASSIFY = False


# ── Data classes ──────────────────────────────────────────────────────────────

@dataclass
class PCBComponent:
    designator: str
    comp_type:  str
    x_mm:       float
    y_mm:       float
    rotation:   float
    footprint:  str
    layer:      str


@dataclass
class DetectedComponent:
    comp_type:     str      # RESISTANCE / CONDENSATEUR / LED / IC / SMD_PASSIVE / CONNECTOR / ELCAP / INCONNU
    cx:            int
    cy:            int
    width:         int
    height:        int
    angle:         float
    confidence:    float
    pad_count:     int      # number of bright pads detected
    pad_positions: list     # [(x,y), ...]
    angle_method:  str      # "pads" | "pca" | "bbox"
    pin1_side:     str = "unknown"  # "left" | "right" | "unknown" — SOIC pin-1 hint from pad-area asymmetry


@dataclass
class OrientationResult:
    ok:             bool
    component:      DetectedComponent
    pcb_comp:       Optional[PCBComponent]
    current_angle:  float
    required_angle: float
    delta_angle:    float
    action:         str
    message:        str

    def to_json(self) -> str:
        return json.dumps({
            "ok":     self.ok,
            "type":   self.component.comp_type,
            "delta":  round(self.delta_angle, 1),
            "action": self.action,
            "conf":   round(self.component.confidence, 2),
            "method": self.component.angle_method,
            "pads":   self.component.pad_count,
        }) + "\n"

    def to_serial_json(self) -> str:
        return self.to_json()


# ── Type inference from designator / footprint ────────────────────────────────

def _infer_type(designator: str, footprint: str) -> str:
    d = designator.upper().lstrip("0123456789")
    f = footprint.upper()
    if d.startswith("R"):                           return "RESISTANCE"
    if d.startswith("C"):                           return "CONDENSATEUR"
    if d.startswith("LED") or d.startswith("D"):   return "LED"
    if d.startswith("U") or d.startswith("IC"):    return "IC"
    if d.startswith("J") or d.startswith("CN") or d.startswith("USB"):
        return "CONNECTOR"
    if any(s in f for s in ("SOIC","SOP","QFP","SOT","TSSOP")):
        return "IC"
    return "SMD_PASSIVE"


# ── PCB loaders ───────────────────────────────────────────────────────────────

def _load_csv(filepath: str) -> dict:
    REF_KEYS   = ("designator","ref","reference","refdes")
    X_KEYS     = ("x","posx","pos_x","mid x","x(mm)")
    Y_KEYS     = ("y","posy","pos_y","mid y","y(mm)")
    ROT_KEYS   = ("rotation","rot","angle")
    FP_KEYS    = ("footprint","package","val","value")
    LAYER_KEYS = ("layer","side","tb")

    components = {}
    for encoding in ("utf-8","latin-1"):
        try:
            with open(filepath, newline="", encoding=encoding) as f:
                raw = [ln for ln in f if not ln.strip().startswith("#") and ln.strip()]
            for row in csv.DictReader(raw):
                row = {k.strip().lower(): v.strip() for k, v in row.items()}
                des = next((row[k] for k in REF_KEYS if k in row), "")
                if not des:
                    continue
                x   = float(next((row[k] for k in X_KEYS     if k in row), 0) or 0)
                y   = float(next((row[k] for k in Y_KEYS     if k in row), 0) or 0)
                rot = float(next((row[k] for k in ROT_KEYS   if k in row), 0) or 0) % 360
                fp  = next((row[k] for k in FP_KEYS    if k in row), "")
                lay = next((row[k] for k in LAYER_KEYS if k in row), "Top")
                components[des] = PCBComponent(
                    designator=des, comp_type=_infer_type(des, fp),
                    x_mm=x, y_mm=y, rotation=rot, footprint=fp, layer=lay,
                )
            print(f"[PCB] Loaded {len(components)} components from CSV ({encoding})")
            break
        except UnicodeDecodeError:
            continue
        except Exception as e:
            print(f"[PCB] CSV error: {e}")
            break
    return components


def _kicad_extract_blocks(text: str) -> list:
    blocks = []
    for m in re.finditer(r'\(\s*(?:module|footprint)\s+', text):
        start = m.start(); depth = 0
        for i in range(start, len(text)):
            if   text[i] == '(': depth += 1
            elif text[i] == ')':
                depth -= 1
                if depth == 0:
                    blocks.append(text[start:i+1]); break
    return blocks


def _kicad_reference(block: str) -> str:
    m = re.search(r'\(\s*property\s+"Reference"\s+"([^"]+)"', block)
    if m: return m.group(1).strip()
    m = re.search(r'\(\s*fp_text\s+reference\s+([^\s"()\n]+)', block, re.IGNORECASE)
    if m:
        val = m.group(1).strip().strip('"')
        if val.upper() not in ("REFERENCE","VALUE","${REFERENCE}","**"):
            return val
    return ""


def _kicad_position(block: str) -> tuple:
    m = re.search(r'\(\s*at\s+([-\d.]+)\s+([-\d.]+)(?:\s+([-\d.]+))?\s*\)', block)
    return (float(m.group(1)), float(m.group(2)), float(m.group(3) or 0)) if m else (0.,0.,0.)


def _load_kicad_pcb(filepath: str) -> dict:
    try:
        text = Path(filepath).read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        print(f"[PCB] Cannot read .kicad_pcb: {e}"); return {}
    components = {}
    for block in _kicad_extract_blocks(text):
        des = _kicad_reference(block)
        if not des: continue
        x, y, rot = _kicad_position(block)
        fp    = re.search(r'\(\s*(?:module|footprint)\s+"?([^\s"()]+)"?', block)
        fp    = fp.group(1) if fp else ""
        layer_m = re.search(r'\(\s*layer\s+"?([^\s"()]+)"?\s*\)', block)
        layer = "Bottom" if layer_m and layer_m.group(1).startswith("B") else "Top"
        components[des] = PCBComponent(
            designator=des, comp_type=_infer_type(des, fp),
            x_mm=x, y_mm=y, rotation=rot % 360, footprint=fp, layer=layer,
        )
    print(f"[PCB] Loaded {len(components)} components from .kicad_pcb")
    return components


def _create_example_csv(filepath: str):
    target = Path(filepath)
    content = (
        "# PCB Centroid\n"
        "# For SMD components the Rotation column is what matters.\n"
        "# 0805 resistors, caps, and LEDs look identical from below —\n"
        "# the Designator prefix (R/C/LED) is used to confirm part type.\n"
        "Designator,X,Y,Rotation,Layer,Footprint\n"
        "R1,10.0,20.0,0,Top,R_0805\n"
        "R2,15.0,20.0,90,Top,R_0805\n"
        "C1,20.0,20.0,0,Top,C_0805\n"
        "LED1,25.0,20.0,0,Top,LED_0805\n"
        "U1,40.0,30.0,0,Top,SOIC-8\n"
        "U2,60.0,30.0,90,Top,SOIC-16\n"
    )
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        print(f"[PCB] Example CSV written to '{filepath}'")
    except Exception as e:
        fallback = Path.cwd() / target.name
        print(f"[PCB] Failed to write example CSV to '{filepath}': {e}")
        try:
            fallback.write_text(content, encoding="utf-8")
            print(f"[PCB] Fallback example CSV written to '{fallback}'")
        except Exception as exc:
            print(f"[PCB] Failed to write fallback example CSV to '{fallback}': {exc}")


def _load_pos(filepath: str) -> dict:
    """
    Load a KiCad .pos footprint position file.
    Format: whitespace-separated, # comments, header line also starts with #.
    Columns: Ref  Val  Package  PosX  PosY  Rot  Side
    """
    components = {}
    for encoding in ("utf-8", "latin-1"):
        try:
            with open(filepath, newline="", encoding=encoding) as f:
                lines = f.readlines()
            for line in lines:
                stripped = line.strip()
                if not stripped or stripped.startswith("#"):
                    continue
                parts = stripped.split()
                if len(parts) < 6:
                    continue
                ref, val, package = parts[0], parts[1], parts[2]
                try:
                    posx = float(parts[3])
                    posy = float(parts[4])
                    rot  = float(parts[5]) % 360
                except ValueError:
                    continue
                layer = "Bottom" if (len(parts) > 6 and parts[6].lower() == "bottom") else "Top"
                components[ref] = PCBComponent(
                    designator=ref, comp_type=_infer_type(ref, package),
                    x_mm=posx, y_mm=posy, rotation=rot,
                    footprint=package, layer=layer,
                )
            print(f"[PCB] Loaded {len(components)} components from .pos ({encoding})")
            break
        except Exception as e:
            print(f"[PCB] .pos error: {e}")
            break
    return components


def load_pcb(filepath: str) -> dict:
    path = Path(filepath)
    if not path.exists():
        alt_csv = Path.cwd() / path.with_suffix(".csv").name
        alt_pos = Path.cwd() / path.with_suffix(".pos").name
        if alt_csv.exists():
            print(f"[PCB] '{filepath}' not found — loading local fallback '{alt_csv}'")
            return _load_csv(str(alt_csv))
        if alt_pos.exists():
            print(f"[PCB] '{filepath}' not found — loading local fallback '{alt_pos}'")
            return _load_pos(str(alt_pos))
        print(f"[PCB] '{filepath}' not found — creating example")
        _create_example_csv(str(path.with_suffix(".csv")))
        return {}
    suffix = path.suffix.lower()
    if suffix == ".kicad_pcb":
        return _load_kicad_pcb(filepath)
    if suffix == ".pos":
        return _load_pos(filepath)
    return _load_csv(filepath)


# ── PCA orientation (works on any mask) ──────────────────────────────────────

def _pca_orientation(mask: np.ndarray) -> Optional[float]:
    """Principal axis angle of white pixels — [0, 180) degrees."""
    pts = np.column_stack(np.where(mask > 0))
    if len(pts) < 10:
        return None
    _, eigvec = cv2.PCACompute(pts.astype(np.float32), mean=None)
    vy, vx = float(eigvec[0, 0]), float(eigvec[0, 1])
    return float(np.degrees(np.arctan2(vy, vx)) % 180)


# ── VisionPipeline ────────────────────────────────────────────────────────────

class VisionPipeline:
    """
    SMD bottom-vision pipeline.

    Stages
    ------
    1  Preprocess   : HSV-Value + CLAHE + Gaussian blur
    2  PadMask      : high-threshold to isolate shiny solder pads
    3  BodyMask     : Otsu / manual threshold for full component silhouette
    4  FindContour  : closest valid contour to frame centre
    5  FindPads     : connected-component blobs on pad mask inside body ROI
    6  Classify     : pad count + shape → component type
    7  Orient       : pad-axis → PCA → bbox fallback
    """

    def __init__(self, manual_thresh: int = 0):
        self.manual_thresh    = manual_thresh
        self._last_pad_mask   = None
        self._last_color_hint = 'unknown'
        self._last_debug_info = {}   # all intermediate metrics for the debug panel

    def run(self, frame: np.ndarray) -> tuple:
        blur, v_eq        = self._preprocess(frame)
        pad_mask          = self._pad_mask(blur)
        body_mask         = self._body_mask(blur)
        contour           = self._find_contour(body_mask)
        if contour is None:
            self._last_pad_mask = pad_mask
            return None, body_mask
        component = self._classify_and_orient(contour, v_eq, pad_mask, frame)
        return component, body_mask

    # ── Stage 1 ──────────────────────────────────────────────────────────────

    def _preprocess(self, frame: np.ndarray) -> tuple:
        hsv   = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        v_eq  = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8,8)).apply(hsv[:,:,2])
        blur  = cv2.GaussianBlur(v_eq, (5, 5), 0)
        return blur, v_eq

    # ── Stage 2 — pad mask ────────────────────────────────────────────────────

    def _pad_mask(self, blur: np.ndarray) -> np.ndarray:
        """
        Isolate shiny solder pads by extracting small bright features using a
        morphological top-hat (difference between image and opened image).
        This suppresses large bright bodies (component bodies) and highlights
        small bright pads. Falls back to percentile threshold when top-hat
        yields no signal.
        """
        # Small-kernel opening to remove speckle
        small_k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
        opened = cv2.morphologyEx(blur, cv2.MORPH_OPEN, small_k, iterations=1)

        # Top-hat with a larger kernel to remove large bright regions (the body)
        large_k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (21, 21))
        top = cv2.morphologyEx(blur, cv2.MORPH_TOPHAT, large_k)

        # If top-hat produced any signal, threshold it; otherwise fallback
        if np.count_nonzero(top) > 5:
            tval = int(np.percentile(top[top > 0], 85)) if np.count_nonzero(top > 0) > 0 else 15
            _, mask = cv2.threshold(top, max(10, tval), 255, cv2.THRESH_BINARY)
        else:
            base = int(np.percentile(blur, 88))
            thresh = min((self.manual_thresh + 40) if self.manual_thresh > 0 else base, 254)
            _, mask = cv2.threshold(blur, thresh, 255, cv2.THRESH_BINARY)

        # Clean small noise and fill pad shapes so rectangular pads survive.
        k = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, k, iterations=1)
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, k, iterations=1)
        return mask

    # ── Stage 3 — body mask ───────────────────────────────────────────────────

    def _body_mask(self, blur: np.ndarray) -> np.ndarray:
        """
        Full component silhouette via Otsu or manual threshold.
        Auto-inverts when background is brighter than the component.
        Uses a heavier blur to suppress surface texture, computes Otsu from
        center-circle pixels only, then clips the mask to that circle so
        peripheral background pixels can never form a connected blob.
        """
        h, w = blur.shape
        cx0, cy0 = w // 2, h // 2
        roi_circle = np.zeros((h, w), dtype=np.uint8)
        cv2.circle(roi_circle, (cx0, cy0), CENTER_RADIUS, 255, -1)

        # Heavier blur than the pad-detection stage to kill surface texture
        blur_body = cv2.GaussianBlur(blur, (11, 11), 0)

        if self.manual_thresh > 0:
            _, mask = cv2.threshold(blur_body, self.manual_thresh, 255, cv2.THRESH_BINARY)
        else:
            center_pixels = blur_body[roi_circle > 0].reshape(-1, 1)
            otsu_val, _ = cv2.threshold(center_pixels, 0, 255,
                                        cv2.THRESH_BINARY + cv2.THRESH_OTSU)
            _, mask = cv2.threshold(blur_body, int(otsu_val), 255, cv2.THRESH_BINARY)

        # Clip to search zone — peripheral pixels can no longer join the blob
        mask = cv2.bitwise_and(mask, roi_circle)

        center_total = int(np.count_nonzero(roi_circle))
        center_white = int(np.count_nonzero(mask))
        if center_total > 0 and center_white / center_total > 0.60:
            mask = cv2.bitwise_not(mask)
            mask = cv2.bitwise_and(mask, roi_circle)

        k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3,3))
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, k, iterations=2)
        return cv2.morphologyEx(mask, cv2.MORPH_OPEN, k, iterations=1)

    # ── Stage 4 — find contour ────────────────────────────────────────────────

    def _find_contour(self, body_mask: np.ndarray) -> Optional[np.ndarray]:
        contours, _ = cv2.findContours(body_mask, cv2.RETR_EXTERNAL,
                                        cv2.CHAIN_APPROX_SIMPLE)
        cx0, cy0 = FRAME_WIDTH // 2, FRAME_HEIGHT // 2
        best, best_dist = None, float("inf")
        for cnt in contours:
            area = cv2.contourArea(cnt)
            if not (BODY_MIN_AREA < area < BODY_MAX_AREA):
                continue
            M = cv2.moments(cnt)
            if M["m00"] == 0: continue
            cx = int(M["m10"] / M["m00"])
            cy = int(M["m01"] / M["m00"])
            dist = float(np.hypot(cx - cx0, cy - cy0))
            if dist > CENTER_RADIUS: continue
            if dist < best_dist:
                best_dist = dist; best = cnt
        return best

    # ── Stage 5 — find pads ───────────────────────────────────────────────────

    def _find_pads_raw(self, pad_mask_roi: np.ndarray,
                       x_off: int, y_off: int) -> list:
        """Returns list of (x, y, area) tuples — kept internal for pin-1 detection."""
        # Try contour-based detection to respect rectangular pad shapes and
        # avoid assuming circular blobs. Apply a light erosion to separate
        # pads that touch due to reflections, then find contours and filter
        # by area / aspect / solidity.
        result = []
        try:
            roi = pad_mask_roi.copy()
            # Light opening to remove speckle noise but preserve small pads
            ker = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
            roi = cv2.morphologyEx(roi, cv2.MORPH_OPEN, ker, iterations=1)

            # Fill small holes inside pads and merge slightly fragmented pad blobs.
            ker_close = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))
            roi = cv2.morphologyEx(roi, cv2.MORPH_CLOSE, ker_close, iterations=1)

            contours, _ = cv2.findContours(roi, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            for cnt in contours:
                area = int(cv2.contourArea(cnt))
                if not (PAD_MIN_AREA < area < PAD_MAX_AREA):
                    continue
                x, y, w, h = cv2.boundingRect(cnt)
                rect_area = max(1, w * h)
                solidity = float(area) / rect_area
                aspect = float(w) / h if h > 0 else 10.0
                # Accept rectangular-ish pads: reasonable aspect and solidity
                if solidity < 0.08:
                    continue
                if not (0.15 <= aspect <= 8.0):
                    if area < PAD_MIN_AREA * 2:
                        continue
                M = cv2.moments(cnt)
                if M.get('m00', 0) == 0:
                    cx = x + w // 2
                    cy = y + h // 2
                else:
                    cx = int(M['m10'] / M['m00'])
                    cy = int(M['m01'] / M['m00'])
                result.append((int(cx) + x_off, int(cy) + y_off, area))

            # Drop spurious small blobs — keep those >= 20% of the largest found.
            if result:
                max_area = max(p[2] for p in result)
                result = [p for p in result if p[2] >= max_area * 0.20]
        except Exception:
            # Fallback to previous connected-components if something fails
            n, _, stats, centroids = cv2.connectedComponentsWithStats(
                pad_mask_roi, connectivity=8)
            for i in range(1, n):
                area = int(stats[i, cv2.CC_STAT_AREA])
                if not (PAD_MIN_AREA < area < PAD_MAX_AREA):
                    continue
                cx, cy = centroids[i]
                result.append((int(cx) + x_off, int(cy) + y_off, area))

        return result

    def _find_pads(self, pad_mask_roi: np.ndarray,
                   x_off: int, y_off: int) -> list:
        """
        Connected-component blob detection on the pad mask cropped to the
        component ROI.  Much more reliable than Hough circles for SMD pads
        because pads are rectangular, not circular.
        """
        return [(x, y) for x, y, _ in self._find_pads_raw(pad_mask_roi, x_off, y_off)]

    def _compute_pin1_side(self, pads_raw: list, bbox_angle: float,
                           cx: float, cy: float) -> str:
        """
        Estimate which end of an IC has pin 1 by comparing total pad area on
        each side of the component's centre along its long axis.

        Reliability note: bottom-view pin-1 detection is inherently unreliable
        for symmetric SOIC packages. The feeder orientation is the real source
        of truth — this hint is advisory only. Returns "unknown" whenever the
        pad-area asymmetry is below PIN1_AREA_ASYM_THRESH (10%).
        "left" / "right" refer to the direction along the long axis after
        projecting onto it; "left" means negative projection (left end when the
        long axis is drawn left-to-right at angle=0).
        """
        PIN1_AREA_ASYM_THRESH = 0.10

        if len(pads_raw) < 6:
            return "unknown"

        angle_rad = np.radians(bbox_angle % 180)
        cos_a, sin_a = np.cos(angle_rad), np.sin(angle_rad)

        left_area = right_area = 0
        for px, py, area in pads_raw:
            proj = (px - cx) * cos_a + (py - cy) * sin_a
            if proj < 0:
                left_area += area
            else:
                right_area += area

        total = left_area + right_area
        if total == 0:
            return "unknown"

        ratio = abs(left_area - right_area) / total
        if ratio < PIN1_AREA_ASYM_THRESH:
            return "unknown"

        # Larger-area side is typically pin 1 (some ICs have a slightly wider pad 1)
        return "left" if left_area > right_area else "right"

    def is_consistent_with(self, detected: "DetectedComponent",
                            footprint: str) -> tuple:
        """
        Check whether `detected` is geometrically plausible for `footprint`.
        Returns (ok: bool, reason: str).

        Uses FOOTPRINT_ENVELOPES from footprint_envelopes.py.
        If no envelope is defined for the footprint, returns (True, "no envelope").
        The check is intentionally wide — only gross mismatches (empty nozzle,
        wrong reel) should fail.
        """
        from footprint_envelopes import get_envelope
        env = get_envelope(footprint)
        if env is None:
            return True, "no envelope defined — skipping check"

        min_pads, max_pads = env.pad_count_range
        # Only flag a pad-count mismatch when pads were actually detected.
        # nb_pads==0 means the pipeline couldn't resolve pads (lighting, distance)
        # — treat as "uncertain" rather than "wrong part".
        if detected.pad_count > 0 and not (min_pads <= detected.pad_count <= max_pads):
            return (False,
                    f"pad count {detected.pad_count} outside [{min_pads},{max_pads}] "
                    f"for footprint '{footprint}'")

        return True, "ok"

    # ── Stage 5b — Color-based body classification ───────────────────────────

    def _classify_by_color(self, bgr: np.ndarray, contour: np.ndarray) -> str:
        """
        Classify component type from body color in the original BGR frame.

        Observed color signatures (bottom-view camera):
          blue_body   — ceramic capacitor (CC0805): whole body is blue/teal
          teal_marker — LED (KPT-2012SURCK): dark body with small green/teal triangle cathode marker
          dark_body   — resistor or IC: uniformly black body

        OpenCV HSV hue 0-179; hue 50-130 covers green→teal→blue.
        """
        if bgr is None or contour is None:
            return 'unknown'

        mask = np.zeros(bgr.shape[:2], dtype=np.uint8)
        cv2.drawContours(mask, [contour], -1, 255, -1)
        body_px = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)[mask > 0]

        if len(body_px) < 20:
            return 'unknown'

        sat_vis = (body_px[:, 1] > 60) & (body_px[:, 2] > 25) & (body_px[:, 2] < 240)
        # H 100-130 = cyan/blue  → ceramic cap body (H≈110-125)
        # H  50-99  = green/teal → LED cathode triangle (H≈60-80); white LED
        #             body pixels are excluded by sat_vis (S≈0 on white)
        colored_blue  = sat_vis & (body_px[:, 0] >= 100) & (body_px[:, 0] <= 130)
        colored_green = sat_vis & (body_px[:, 0] >= 50)  & (body_px[:, 0] <= 99)

        blue_ratio  = float(np.sum(colored_blue))  / len(body_px)
        green_ratio = float(np.sum(colored_green)) / len(body_px)

        self._last_color_hint = (
            'blue_body'   if blue_ratio  > 0.25 else
            'teal_marker' if green_ratio > 0.01 else
            'dark_body'
        )
        self._last_debug_info['blue_ratio']  = blue_ratio
        self._last_debug_info['green_ratio'] = green_ratio
        self._last_debug_info['hue_values']  = body_px[:, 0].copy()
        return self._last_color_hint

    # ── Stage 5c — LED vs resistor/capacitor ─────────────────────────────────

    def _led_or_passive(self, contour: np.ndarray, gray: np.ndarray) -> tuple:
        """
        Distinguish an 0805 LED from a resistor or capacitor using the polarity
        marking on the body: dark cathode band / triangle vs uniform passive body.

        The body is rotated so the long axis is horizontal, then a central strip
        is sampled excluding the extreme ends.  The left/right brightness delta
        is normalized and smoothed to reduce sensitivity to pad reflections.
        """
        rect = cv2.minAreaRect(contour)
        (cx, cy), (w, h), angle = rect
        if w < h:
            w, h = h, w
            angle += 90

        if w < 6 or h < 2:
            return "SMD_PASSIVE", 0.50

        # Rotate so the long axis is horizontal
        M = cv2.getRotationMatrix2D((cx, cy), angle, 1.0)
        v_rot = cv2.warpAffine(gray, M, (gray.shape[1], gray.shape[0]),
                               flags=cv2.INTER_LINEAR)

        pad_inset = max(1, int(w * 0.12))
        half_h    = max(1, int(h * 0.25))
        x1 = max(0, int(cx) - int(w // 2) + pad_inset)
        x2 = min(v_rot.shape[1], int(cx) + int(w // 2) - pad_inset)
        y1 = max(0, int(cy) - half_h)
        y2 = min(v_rot.shape[0], int(cy) + half_h)

        strip = v_rot[y1:y2, x1:x2]
        if strip.size < 20:
            return "SMD_PASSIVE", 0.50

        strip = cv2.GaussianBlur(strip, (3, 3), 0)
        mid = strip.shape[1] // 2
        left_mean = float(np.mean(strip[:, :mid]))
        right_mean = float(np.mean(strip[:, mid:]))
        asymmetry = abs(left_mean - right_mean)
        ratio = max(left_mean, right_mean) / max(1.0, min(left_mean, right_mean))

        if asymmetry >= LED_BODY_ASYM_THRESH or ratio >= 1.03:
            return "LED", 0.70
        return "SMD_PASSIVE", 0.75

    def _infer_passive_type(self, contour: np.ndarray, pads_raw: list, ratio: float) -> str:
        """
        Infer whether a dark-bodied 2-pad part is a capacitor or resistor.

        Key observation:
        - Resistor (0805): elongated rectangular body, ratio w/h >> 1.5
        - Capacitor: more compact/rounded body, ratio w/h ~ 1.2–1.4

        Also consider pad area relative to body: capacitors often have larger
        pad terminations extending inward from the body edges.
        """
        if len(pads_raw) != 2:
            return "SMD_PASSIVE"
        body_area = cv2.contourArea(contour)
        pad_total_area = float(pads_raw[0][2] + pads_raw[1][2])
        area_ratio = pad_total_area / max(body_area, 1.0)

    # 2. Calcul de la distance entre les pads par rapport à la taille totale
    # On calcule la distance entre les centres des deux pads
        p0, p1 = pads_raw[0], pads_raw[1]
        dist_centers = float(np.hypot(p1[0] - p0[0], p1[1] - p0[1]))
    
    # On récupère la longueur totale (w) du composant
        rect = cv2.minAreaRect(contour)
        _, (w, h), _ = rect
        length = max(w, h)

    # Metric de profondeur : plus le ratio est PETIT, plus les pads sont vers l'INTÉRIEUR
        depth_ratio = dist_centers / max(length, 1.0)

    # LOGIQUE DE DÉCISION :
    # Si le ratio_area est élevé (> 7%) OU si les pads sont proches (depth_ratio < 0.65)
    # alors c'est un condensateur.
    
    # Note : Ajuste le 0.68 ci-dessous selon tes tests
    # Résistance typique : depth_ratio environ 0.75 - 0.80
    # Condensateur typique : depth_ratio environ 0.55 - 0.65
    
        self._last_debug_info.update({'depth_ratio': depth_ratio,
                                      'area_ratio': area_ratio,
                                      'dist_centers': dist_centers})
        if depth_ratio < 0.68 or area_ratio > 0.08:
            return "CONDENSATEUR"
        else:
            return "RESISTANCE"

    # ── Stage 5d — LED cathode-end detection ─────────────────────────────────

    def _cathode_at_p1(self, gray: np.ndarray, p0: tuple, p1: tuple) -> Optional[bool]:
        """
        Determine which pad terminal is the cathode by sampling body brightness
        just inside each pad (toward the centre of the component).

        The cathode end of an 0805 LED has a dark marking visible from below —
        a printed band, T-mark, or triangle arrow.  That darker body region
        near one pad is the signal we use.

        Convention: angle will be set to point FROM the anode pad TOWARD the
        cathode pad (i.e. in the p0→p1 direction when this returns True).
        Verify once against your KiCad footprint; if consistently 180° off,
        swap the P1/P0 assignment below.

        Returns True  if p1 is cathode (angle already correct),
                False if p0 is cathode (angle needs +180°),
                None  if the brightness difference is too small to be reliable.
        """
        CATHODE_MIN_DIFF = 8   # intensity units — below this we can't tell

        L = float(np.hypot(p1[0] - p0[0], p1[1] - p0[1]))
        if L < 10:
            return None

        ux = (p1[0] - p0[0]) / L
        uy = (p1[1] - p0[1]) / L
        inset = max(6, int(L * 0.18))   # how far inside each pad to sample

        def body_brightness(origin_x: float, origin_y: float,
                            step_x: float, step_y: float) -> float:
            """Mean pixel value of a small region inset from a pad terminal."""
            vals = []
            for k in (0.4, 0.7, 1.0):
                sx = int(origin_x + k * inset * step_x)
                sy = int(origin_y + k * inset * step_y)
                r  = 3
                x1 = max(0, sx - r);  y1 = max(0, sy - r)
                x2 = min(gray.shape[1] - 1, sx + r)
                y2 = min(gray.shape[0] - 1, sy + r)
                roi = gray[y1:y2 + 1, x1:x2 + 1]
                if roi.size > 0:
                    vals.append(float(np.mean(roi)))
            return float(np.mean(vals)) if vals else 128.0

        # Step inward from each pad toward the component centre
        b_near_p1 = body_brightness(p1[0], p1[1], -ux, -uy)
        b_near_p0 = body_brightness(p0[0], p0[1],  ux,  uy)

        diff = b_near_p1 - b_near_p0
        if abs(diff) < CATHODE_MIN_DIFF:
            return None          # marking not visible — fall back to axis-only

        return diff < 0          # darker near p1 → cathode band at p1

    def _cathode_from_green_marker(self, bgr: np.ndarray, contour: np.ndarray,
                                   p0: tuple, p1: tuple) -> Optional[bool]:
        """
        Locate the green cathode triangle within the LED body and determine
        which pad it is closest to.

        The KPT-2012SURCK has a green triangle printed near the cathode pad.
        We find the centroid of green pixels (H 50-99) inside the body contour
        and compare its distance to p0 vs p1.

        Returns True  if cathode is at p1,
                False if cathode is at p0,
                None  if marker not found or position is ambiguous.
        """
        if bgr is None or contour is None:
            return None

        mask = np.zeros(bgr.shape[:2], dtype=np.uint8)
        cv2.drawContours(mask, [contour], -1, 255, -1)

        hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
        green = (
            (hsv[:, :, 0] >= 50) & (hsv[:, :, 0] <= 99) &
            (hsv[:, :, 1] > 60) &
            (hsv[:, :, 2] > 25) & (hsv[:, :, 2] < 240)
        )
        body_px_count = int(np.count_nonzero(mask))
        pts = np.argwhere(green & (mask > 0))   # (y, x)
        # Require enough green pixels AND at least 2% of body area —
        # avoids false triggers from polarity stripes on electrolytic caps.
        if len(pts) < 15 or (body_px_count > 0 and len(pts) / body_px_count < 0.02):
            return None

        gx = float(np.mean(pts[:, 1]))
        gy = float(np.mean(pts[:, 0]))

        pad_dist = float(np.hypot(p1[0] - p0[0], p1[1] - p0[1]))
        if pad_dist == 0:
            return None

        d0 = float(np.hypot(gx - p0[0], gy - p0[1]))
        d1 = float(np.hypot(gx - p1[0], gy - p1[1]))

        # Marker centroid must be clearly on one side (> 10% of pad spacing)
        if abs(d0 - d1) < pad_dist * 0.10:
            return None

        return d1 < d0   # True → green marker closer to p1 → cathode at p1

    # ── Stage 6 + 7 — classify and orient ────────────────────────────────────

    def _classify_and_orient(self, contour,
                              gray: np.ndarray,
                              pad_mask: np.ndarray,
                              bgr: np.ndarray = None) -> DetectedComponent:
        rect = cv2.minAreaRect(contour)
        (cx, cy), (w, h), bbox_angle = rect
        if w < h:
            w, h = h, w; bbox_angle += 90

        area        = cv2.contourArea(contour)
        ratio       = w / h if h > 0 else 1.0
        perim       = cv2.arcLength(contour, True)
        circularity = (4 * np.pi * area) / (perim ** 2) if perim > 0 else 0.0
        self._last_debug_info.update({'circularity': circularity, 'ratio': ratio,
                                      'body_area': area})

        # Crop ROI around component
        margin = max(int(w), int(h)) // 2 + 20
        x1 = max(0, int(cx) - margin);  y1 = max(0, int(cy) - margin)
        x2 = min(gray.shape[1], int(cx) + margin)
        y2 = min(gray.shape[0], int(cy) + margin)
        # Local pad mask: threshold within the component ROI so the detection
        # adapts to whatever brightness the pads actually have (gold, silver, tin).
        # The pads are always the brightest things in the local area regardless
        # of absolute brightness — a global percentile can miss silver pads.
        pad_search = np.zeros_like(pad_mask)
        cv2.drawContours(pad_search, [contour], -1, 255, -1)
        body_fill = pad_search.copy()   # body interior only — used for brightness reference
        # Dilate enough to cover IC leads that protrude 15-25px beyond the body outline.
        dil_k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (11, 11))
        pad_search = cv2.dilate(pad_search, dil_k, iterations=3)

        local_gray = gray[y1:y2, x1:x2]
        pad_search_roi = pad_search[y1:y2, x1:x2]
        if local_gray.size > 0:
            # Threshold strategy: pads are always brighter than the body.
            # Use the 92nd percentile of body pixels as the lower bound — this sits
            # just above the body's own bright surface features (works for resistors
            # whose beige body is medium-bright, not just for dark IC bodies).
            # On bright backgrounds apply an upper bound to exclude background pixels
            # that leaked into the dilation margin; on dark backgrounds a lower bound
            # alone is sufficient (background is darker than the pads anyway).
            body_pixels  = local_gray[body_fill[y1:y2, x1:x2] > 0]
            bg_mask_roi  = cv2.bitwise_not(pad_search_roi)
            bg_pixels    = local_gray[bg_mask_roi > 0]
            if body_pixels.size >= 20:
                body_hi   = int(np.percentile(body_pixels, 92))
                thresh_lo = body_hi + 5
            else:
                body_hi   = 0
                thresh_lo = 128
            if bg_pixels.size >= 20:
                bg_low = int(np.percentile(bg_pixels, 30))
            else:
                bg_low = 0
            bright_bg = bg_low > thresh_lo + 20
            if bright_bg:
                thresh_hi = bg_low - 15
                local_pad = cv2.inRange(local_gray, thresh_lo, thresh_hi)
            else:
                thresh_hi = 255
                _, local_pad = cv2.threshold(local_gray, thresh_lo, 255, cv2.THRESH_BINARY)
            self._last_debug_info.update({'body_hi': body_hi, 'thresh_lo': thresh_lo,
                                          'thresh_hi': thresh_hi, 'bg_low': bg_low,
                                          'bright_bg': bright_bg})
            k3 = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
            local_pad = cv2.morphologyEx(local_pad, cv2.MORPH_OPEN, k3, iterations=1)
            roi_pad = cv2.bitwise_and(local_pad, pad_search_roi)
        else:
            roi_pad = cv2.bitwise_and(pad_mask, pad_search)[y1:y2, x1:x2]

        # Store roi_pad in full-frame coordinates so the zoom window shows
        # exactly what the detector used (two-sided threshold, body-masked).
        debug_mask = np.zeros_like(pad_mask)
        debug_mask[y1:y2, x1:x2] = roi_pad
        self._last_pad_mask = debug_mask

        pads_raw  = self._find_pads_raw(roi_pad, x1, y1)
        pads      = [(x, y) for x, y, _ in pads_raw]
        nb_pads   = len(pads)

        # ── Color analysis (original BGR frame, before grayscale processing) ──
        color_hint = self._classify_by_color(bgr, contour)

        # ── Cathode pre-computation for 2-pad components ──────────────────────
        # A non-None result means a brightness asymmetry (polarity marker) is
        # visible between the two pad ends → component is a LED, not a passive.
        # Resistors and capacitors have symmetric ends → returns None.
        # Computing this before classification lets us use it as a LED signal
        # without calling _cathode_at_p1 a second time in the orient block.
        _cathode_pre = (self._cathode_at_p1(gray, pads[0], pads[1])
                        if nb_pads == 2 else None)
        _body_led, _body_conf = (self._led_or_passive(contour, gray)
                                 if nb_pads == 2 else (None, None))
        _passive_type = (self._infer_passive_type(contour, pads_raw, ratio)
                         if color_hint == 'dark_body' and nb_pads == 2 else None)

        # ── Classify ──────────────────────────────────────────────────────────
        comp_type, confidence = "INCONNU", 0.35

        if nb_pads >= 14:
            comp_type, confidence = "IC", 0.85          # SOIC-16 (16 pads)
        elif nb_pads >= 6:
            comp_type, confidence = "IC", 0.80          # SOIC-8  (8 pads)
        elif nb_pads >= 4:
            comp_type, confidence = "CONNECTOR", 0.70   # USB 5-pin or similar
        elif nb_pads == 2:
            if circularity > 0.80:
                # True round body (electrolytic cylinder seen from below ≈ 1.0,
                # 0805 rectangle never exceeds ~0.78 even with blurring)
                comp_type, confidence = "ELCAP", 0.75
            elif color_hint == 'blue_body':
                comp_type, confidence = "CONDENSATEUR", 0.88
            elif color_hint == 'teal_marker' or _cathode_pre is not None or _body_led == "LED":
                comp_type, confidence = "LED", max(0.65, _body_conf or 0.68)
            elif _passive_type == 'CONDENSATEUR':
                comp_type, confidence = "CONDENSATEUR", 0.75
            elif color_hint == 'dark_body':
                comp_type, confidence = "RESISTANCE", 0.72
            else:
                comp_type, confidence = "SMD_PASSIVE", 0.72
        elif nb_pads in (0, 1):
            # Pads not resolved — use color first, then body shape
            if color_hint == 'blue_body':
                comp_type, confidence = "CONDENSATEUR", 0.72
            elif color_hint == 'teal_marker':
                comp_type, confidence = "LED", 0.65
            elif circularity > 0.80:
                comp_type, confidence = "ELCAP", 0.55
            elif color_hint == 'dark_body' and ratio > 1.2:
                # Beige/tan elongated body with no detected pads → most likely a resistor.
                comp_type, confidence = "RESISTANCE", 0.45
            elif ratio > 1.5:
                comp_type, confidence = "SMD_PASSIVE", 0.45
            else:
                comp_type, confidence = "INCONNU", 0.30

        self._last_debug_info.update({'nb_pads': nb_pads, 'comp_type': comp_type,
                                      'confidence': confidence,
                                      'passive_type': _passive_type,
                                      'body_led': _body_led})

        # ── Orient ────────────────────────────────────────────────────────────
        angle, angle_method = float(bbox_angle % 360), "bbox"

        # Best: axis between the two pads (works for 0805 and SOIC)
        if nb_pads == 2:
            p0, p1 = pads[0], pads[1]
            dx, dy = float(p1[0] - p0[0]), float(p1[1] - p0[1])
            raw    = float(np.degrees(np.arctan2(dy, dx)))

            if comp_type == "LED":
                # Primary: green triangle centroid — more reliable than brightness.
                # Fallback: brightness asymmetry near pad ends (_cathode_pre).
                cathode_tip = self._cathode_from_green_marker(bgr, contour, p0, p1)
                if cathode_tip is None:
                    cathode_tip = _cathode_pre
                if cathode_tip is None:
                    angle        = float(raw % 180)
                    angle_method = "pads"
                else:
                    if not cathode_tip:
                        raw += 180                    # cathode is at p0 side
                    angle        = float(raw % 360)
                    angle_method = "pads_led_green"
            else:
                angle        = float(raw % 180)
                angle_method = "pads"

        elif nb_pads >= 4:
            # For ICs: PCA on pad positions gives the long axis
            pad_pts = np.array(pads, dtype=np.float32)
            if len(pad_pts) >= 4:
                _, eigvec = cv2.PCACompute(pad_pts, mean=None)
                vx, vy   = float(eigvec[0, 0]), float(eigvec[0, 1])
                angle    = float(np.degrees(np.arctan2(vy, vx)) % 180)
                angle_method = "pca_pads"

        # Fallback: PCA on the body mask pixels
        if angle_method == "bbox":
            roi_body = (gray[y1:y2, x1:x2] > 30).astype(np.uint8) * 255
            pca = _pca_orientation(roi_body)
            if pca is not None:
                angle, angle_method = pca, "pca"

        # Pin-1 hint for ICs — unreliable from bottom view alone (see _compute_pin1_side)
        pin1_side = "unknown"
        if nb_pads >= 6:
            pin1_side = self._compute_pin1_side(pads_raw, float(bbox_angle), cx, cy)

        if DEBUG_CLASSIFY:
            try:
                pads_areas = [p[2] for p in pads_raw] if pads_raw else []
            except Exception:
                pads_areas = []
            # Optionally compute body asymmetry score via _led_or_passive as additional insight
            try:
                asym_type, asym_conf = self._led_or_passive(contour, gray) if nb_pads == 2 else (None, None)
            except Exception:
                asym_type, asym_conf = (None, None)
            print(
                f"[DEBUG] comp_type_guess={comp_type} nb_pads={nb_pads} pads_areas={pads_areas} "
                f"circularity={circularity:.2f} ratio={ratio:.2f} color_hint={color_hint} "
                f"asym_type={asym_type} asym_conf={asym_conf} angle_method={angle_method}"
            )

        return DetectedComponent(
            comp_type=comp_type,
            cx=int(cx), cy=int(cy),
            width=int(w), height=int(h),
            angle=angle,
            confidence=confidence,
            pad_count=nb_pads,
            pad_positions=pads,
            angle_method=angle_method,
            pin1_side=pin1_side,
        )


# ── Compatibility shims ───────────────────────────────────────────────────────

def preprocess(frame: np.ndarray, manual_thresh: int = 0) -> tuple:
    pipe = VisionPipeline(manual_thresh)
    blur, v_eq = pipe._preprocess(frame)
    return pipe._body_mask(blur), v_eq


def find_nozzle_component(frame: np.ndarray, manual_thresh: int = 0) -> tuple:
    return VisionPipeline(manual_thresh).run(frame)


# ── Orientation check ─────────────────────────────────────────────────────────

def _smallest_delta(detected: float, required: float) -> float:
    delta = (required - detected) % 360
    return delta - 360 if delta > 180 else delta


def check_orientation(component: DetectedComponent,
                      pcb_comp: Optional[PCBComponent]) -> OrientationResult:
    if pcb_comp is None:
        return OrientationResult(
            ok=False, component=component, pcb_comp=None,
            current_angle=component.angle, required_angle=0.0,
            delta_angle=0.0, action="REJECT",
            message="Designator not in PCB data",
        )
    SMD_PASSIVE_TYPES = {"RESISTANCE", "CONDENSATEUR", "LED", "SMD_PASSIVE"}
    type_ok = (
        component.comp_type == pcb_comp.comp_type
        or (component.comp_type == "SMD_PASSIVE" and pcb_comp.comp_type in SMD_PASSIVE_TYPES)
        or component.comp_type == "INCONNU"
        or component.confidence < 0.65
    )
    req_angle_for_reject = (-pcb_comp.rotation) % 360 if pcb_comp.layer == "Bottom" else pcb_comp.rotation
    if not type_ok:
        return OrientationResult(
            ok=False, component=component, pcb_comp=pcb_comp,
            current_angle=component.angle, required_angle=req_angle_for_reject,
            delta_angle=0.0, action="REJECT",
            message=f"WRONG PART: camera={component.comp_type}, PCB={pcb_comp.comp_type}",
        )

    # Mirror rotation for bottom-layer components: the camera sees a flipped image,
    # so the required angle is negated relative to the PCB file value.
    required_angle = (-pcb_comp.rotation) % 360 if pcb_comp.layer == "Bottom" else pcb_comp.rotation

    delta = _smallest_delta(component.angle, required_angle)

    # 0805 passives and SMD passives are 180-symmetric
    symmetric = {"RESISTANCE", "CONDENSATEUR", "SMD_PASSIVE"}
    if component.comp_type in symmetric and abs(delta) > 90:
        alt = delta - 180 if delta > 0 else delta + 180
        if abs(alt) < abs(delta):
            delta = alt

    ok = abs(delta) <= ANGLE_TOLERANCE
    if ok:
        action, message = "PLACE", f"OK — place now (delta={delta:+.1f}°)"
    elif delta > 0:
        action, message = "ROTATE_CW",  f"Rotate CW {delta:.1f}°"
    else:
        action, message = "ROTATE_CCW", f"Rotate CCW {abs(delta):.1f}°"

    return OrientationResult(
        ok=ok, component=component, pcb_comp=pcb_comp,
        current_angle=component.angle, required_angle=required_angle,
        delta_angle=delta, action=action, message=message,
    )


# ── Public entry point ────────────────────────────────────────────────────────

def analyse(frame: np.ndarray,
            designator: str,
            pcb_data: dict,
            manual_thresh: int = 0) -> Optional[OrientationResult]:
    """
    Main entry point.

    Example
    -------
        pcb    = load_pcb("board.csv")
        result = analyse(frame, "R1", pcb)
        if result:
            print(result.to_json())
    """
    comp, _ = find_nozzle_component(frame, manual_thresh)
    if comp is None:
        return None
    return check_orientation(comp, pcb_data.get(designator))
