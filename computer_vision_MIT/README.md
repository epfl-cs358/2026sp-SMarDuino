# Vision PnP — Bottom-View Orientation System

SMD pick-and-place orientation checker for a Prusa MK3S+ conversion.
One upward-facing camera is mounted on the bed; components are inspected
after pickup, before placement.

---

## Files

| File | Purpose |
|---|---|
| `orientation_engine.py` | Detection core — `load_pcb`, `VisionPipeline`, `check_orientation` |
| `footprint_envelopes.py` | Geometric envelopes for WRONG_PART detection |
| `placement_sequencer.py` | Production orchestrator — `PlacementSequencer` |
| `run_placement.py` | Automated placement CLI (production use) |
| `pnp_bottom_vision.py` | Manual debug / tuning tool (keep for calibration) |
| `camera_diagnostic.py` | Threshold-tuning utility |
| `test_pnp_vision.py` | Full test suite (no camera or Arduino required) |
| `pcb_centroid.csv` | Example centroid file |

---

## Quick start

```bash
pip install opencv-python pyserial numpy

# Manual debug tool (keyboard navigation)
python pnp_bottom_vision.py

# Automated placement loop
python run_placement.py

# Run all tests (no hardware required)
python test_pnp_vision.py
```

---

## Sequencer flow

```
load_job(pcb_file)
      │
      ▼
┌─────────────────────────────────────────────────────┐
│  for each component in centroid order:              │
│                                                     │
│  1. Wait for trigger (READY <des> or function call) │
│  2. Capture one frame from bottom camera            │
│  3. VisionPipeline.run(frame) → DetectedComponent   │
│  4. is_consistent_with(detected, footprint)         │
│        ↓ fail → WRONG_PART  halt, operator skip     │
│  5. check_orientation(detected, pcb_comp)           │
│        PLACE     → send JSON, wait ACK, advance     │
│        ROTATE_*  → send JSON, wait ACK, retry once  │
│        REJECT    → WRONG_PART halt                  │
└─────────────────────────────────────────────────────┘
```

### Keyboard shortcuts (run_placement.py)

| Key | Action |
|---|---|
| `SPACE` | Process current expected designator |
| `s` | Skip (operator override after WRONG_PART halt) |
| `r` | Reset queue to start |
| `d` | Toggle debug body-mask overlay |
| `q` | Quit |

---

## Serial protocol

### Host → Arduino (one JSON line per component)

```json
{"des":"R1","ok":true,"type":"SMD_PASSIVE","delta":-12.3,"action":"ROTATE_CCW","conf":0.72,"method":"pads","pads":2,"pin1":"unknown"}
```

| Field | Type | Description |
|---|---|---|
| `des` | string | Designator from centroid file |
| `ok` | bool | `true` when within ANGLE_TOLERANCE (5°) |
| `type` | string | Detected component class |
| `delta` | float | Signed rotation needed (CCW-positive, degrees) |
| `action` | string | `PLACE` / `ROTATE_CW` / `ROTATE_CCW` / `REJECT` / `WRONG_PART` |
| `conf` | float | Detection confidence 0–1 |
| `method` | string | Angle method used: `pads` / `pca_pads` / `pca` / `bbox` |
| `pads` | int | Pad count detected |
| `pin1` | string | `"left"` / `"right"` / `"unknown"` — IC pin-1 hint |

### Arduino → Host (ACK)

```
ACK R1 PLACED
ACK U2 ROTATED
ACK R3 ERROR
```

The sequencer blocks up to **5 seconds** for the ACK.  Timeout or `ERROR`
halts the sequence and requires operator intervention.

---

## Rotation convention

KiCad uses **CCW-positive degrees**.  The engine preserves this end-to-end.
The Arduino receives the same sign convention — positive delta means rotate
counter-clockwise.

For components placed on the **bottom layer**, the required rotation is
mirrored: `required_angle = (-pcb_rotation) % 360`.

---

## Footprint envelopes (WRONG_PART detection)

`footprint_envelopes.py` maps footprint strings to geometric envelopes
`(pad_count_range, aspect_ratio_min, body_area_range)`.  Matching is by
case-insensitive substring, so `"R_0805"` matches both `"R_0805"` and
`"Resistor_SMD:R_0805_2012Metric"`.

If no envelope is defined for a footprint the check is skipped (proceed).
To add a new footprint, add an entry to `FOOTPRINT_ENVELOPES` in
`footprint_envelopes.py`.

---

## Pin-1 detection (IC packages)

Bottom-view pin-1 detection for symmetric SOIC packages is **inherently
unreliable** — many ICs look identical from below.  The sequencer surfaces a
`pin1_side` hint (`"left"` / `"right"` / `"unknown"`) based on pad-area
asymmetry, but proceeds regardless of the result.  The feeder orientation is
the real source of truth.  A top camera would make this reliable; we can
revisit when/if one is added.

---

## Running tests

```bash
python test_pnp_vision.py
```

Runs three suites:

1. **Vision tests** — synthetic frames through the full detection pipeline
2. **Loader tests** — CSV and KiCad PCB file parsers
3. **Sequencer tests** — `PlacementSequencer` in offline mode (no hardware)

All tests run without a camera or Arduino.
