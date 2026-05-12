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
PAD_MIN_AREA    = 8       # px² — ignore tiny noise blobs
PAD_MAX_AREA    = 4000    # px² — ignore large body reflections

# Body contour size limits
BODY_MIN_AREA   = 80      # px² — smallest valid 0805 at close range
BODY_MAX_AREA   = 200_000 # px²

# LED cathode-band detector
LED_BODY_ASYM_THRESH = 15  # intensity units — min left/right brightness difference to flag LED


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
    Path(filepath).write_text(
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
        "U2,60.0,30.0,90,Top,SOIC-16\n",
        encoding="utf-8",
    )
    print(f"[PCB] Example CSV written to '{filepath}'")


def load_pcb(filepath: str) -> dict:
    path = Path(filepath)
    if not path.exists():
        print(f"[PCB] '{filepath}' not found — creating example")
        _create_example_csv(str(path.with_suffix(".csv")))
        return {}
    return _load_kicad_pcb(filepath) if path.suffix.lower() == ".kicad_pcb" else _load_csv(filepath)


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
        self.manual_thresh = manual_thresh

    def run(self, frame: np.ndarray) -> tuple:
        blur, v_eq        = self._preprocess(frame)
        pad_mask          = self._pad_mask(blur)
        body_mask         = self._body_mask(blur)
        contour           = self._find_contour(body_mask)
        if contour is None:
            return None, body_mask
        component = self._classify_and_orient(contour, v_eq, pad_mask)
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
        Isolate shiny solder pads by thresholding the brightest pixels.
        Uses the 80th percentile as a lower bound so it adapts to lighting.
        Manual thresh shifts this up by 40 if set.
        """
        base = int(np.percentile(blur, 80))
        thresh = min((self.manual_thresh + 40) if self.manual_thresh > 0 else base, 254)
        _, mask = cv2.threshold(blur, thresh, 255, cv2.THRESH_BINARY)
        k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3,3))
        return cv2.morphologyEx(mask, cv2.MORPH_OPEN, k, iterations=1)

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

    def _find_pads(self, pad_mask_roi: np.ndarray,
                   x_off: int, y_off: int) -> list:
        """
        Connected-component blob detection on the pad mask cropped to the
        component ROI.  Much more reliable than Hough circles for SMD pads
        because pads are rectangular, not circular.
        """
        n, labels, stats, centroids = cv2.connectedComponentsWithStats(
            pad_mask_roi, connectivity=8)
        pads = []
        for i in range(1, n):  # 0 = background
            area = int(stats[i, cv2.CC_STAT_AREA])
            if not (PAD_MIN_AREA < area < PAD_MAX_AREA):
                continue
            cx, cy = centroids[i]
            pads.append((int(cx) + x_off, int(cy) + y_off))
        return pads

    # ── Stage 5b — LED vs resistor/capacitor ─────────────────────────────────

    def _led_or_passive(self, contour: np.ndarray, gray: np.ndarray) -> tuple:
        """
        Distinguish an 0805 LED from a resistor or capacitor using the cathode
        polarity band — a darker stripe printed on one end of the LED body that
        is visible from below.

        Method: rotate the component body so its long axis is horizontal, take
        a thin central strip of pixels (avoids pad reflections at the ends),
        and compare mean brightness of the left half vs the right half.
        A difference above LED_BODY_ASYM_THRESH indicates the polarity band.

        Cannot distinguish resistor from capacitor — both return SMD_PASSIVE.

        Returns (comp_type, confidence).
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

        # Central strip: middle 60% of body length (skip pad-reflection zones at
        # each end), middle 40% of height (avoid edge fringing)
        pad_inset = max(1, int(w * 0.20))
        half_h    = max(1, int(h * 0.20))
        x1 = max(0, int(cx) - int(w // 2) + pad_inset)
        x2 = min(v_rot.shape[1], int(cx) + int(w // 2) - pad_inset)
        y1 = max(0, int(cy) - half_h)
        y2 = min(v_rot.shape[0], int(cy) + half_h)

        strip = v_rot[y1:y2, x1:x2]
        if strip.size < 20:
            return "SMD_PASSIVE", 0.50

        mid        = strip.shape[1] // 2
        left_mean  = float(np.mean(strip[:, :mid]))
        right_mean = float(np.mean(strip[:, mid:]))
        asymmetry  = abs(left_mean - right_mean)

        if asymmetry >= LED_BODY_ASYM_THRESH:
            return "LED", 0.68   # cathode band detected
        return "SMD_PASSIVE", 0.72

    # ── Stage 6 + 7 — classify and orient ────────────────────────────────────

    def _classify_and_orient(self, contour,
                              gray: np.ndarray,
                              pad_mask: np.ndarray) -> DetectedComponent:
        rect = cv2.minAreaRect(contour)
        (cx, cy), (w, h), bbox_angle = rect
        if w < h:
            w, h = h, w; bbox_angle += 90

        area        = cv2.contourArea(contour)
        ratio       = w / h if h > 0 else 1.0
        perim       = cv2.arcLength(contour, True)
        circularity = (4 * np.pi * area) / (perim ** 2) if perim > 0 else 0.0

        # Crop ROI around component
        margin = max(int(w), int(h)) // 2 + 20
        x1 = max(0, int(cx) - margin);  y1 = max(0, int(cy) - margin)
        x2 = min(gray.shape[1], int(cx) + margin)
        y2 = min(gray.shape[0], int(cy) + margin)
        roi_pad  = pad_mask[y1:y2, x1:x2]

        pads     = self._find_pads(roi_pad, x1, y1)
        nb_pads  = len(pads)

        # ── Classify ──────────────────────────────────────────────────────────
        comp_type, confidence = "INCONNU", 0.35

        if nb_pads >= 14:
            comp_type, confidence = "IC", 0.85          # SOIC-16 (16 pads)
        elif nb_pads >= 6:
            comp_type, confidence = "IC", 0.80          # SOIC-8  (8 pads)
        elif nb_pads >= 4:
            comp_type, confidence = "CONNECTOR", 0.70   # USB 5-pin or similar
        elif nb_pads == 2:
            if circularity > 0.65:
                comp_type, confidence = "ELCAP", 0.75   # round electrolytic
            else:
                comp_type, confidence = self._led_or_passive(contour, gray)
        elif nb_pads in (0, 1):
            # Pads not resolved — too far, bad lighting, or very small component
            # Fall back to body shape only
            if circularity > 0.65:
                comp_type, confidence = "ELCAP", 0.55
            elif ratio > 1.5:
                comp_type, confidence = "SMD_PASSIVE", 0.45
            else:
                comp_type, confidence = "INCONNU", 0.30

        # ── Orient ────────────────────────────────────────────────────────────
        angle, angle_method = float(bbox_angle % 360), "bbox"

        # Best: axis between the two pads (works for 0805 and SOIC)
        if nb_pads == 2:
            p0, p1 = pads[0], pads[1]
            dx, dy = float(p1[0] - p0[0]), float(p1[1] - p0[1])
            angle  = float(np.degrees(np.arctan2(dy, dx)) % 180)
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

        return DetectedComponent(
            comp_type=comp_type,
            cx=int(cx), cy=int(cy),
            width=int(w), height=int(h),
            angle=angle,
            confidence=confidence,
            pad_count=nb_pads,
            pad_positions=pads,
            angle_method=angle_method,
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

    # SMD_PASSIVE is acceptable for R / C / LED in the PCB file
    SMD_PASSIVE_TYPES = {"RESISTANCE", "CONDENSATEUR", "LED", "SMD_PASSIVE"}
    type_ok = (
        component.comp_type == pcb_comp.comp_type
        or (component.comp_type == "SMD_PASSIVE" and pcb_comp.comp_type in SMD_PASSIVE_TYPES)
        or component.comp_type == "INCONNU"
        or component.confidence < 0.65
    )
    if not type_ok:
        return OrientationResult(
            ok=False, component=component, pcb_comp=pcb_comp,
            current_angle=component.angle, required_angle=pcb_comp.rotation,
            delta_angle=0.0, action="REJECT",
            message=f"WRONG PART: camera={component.comp_type}, PCB={pcb_comp.comp_type}",
        )

    delta = _smallest_delta(component.angle, pcb_comp.rotation)

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
        current_angle=component.angle, required_angle=pcb_comp.rotation,
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
