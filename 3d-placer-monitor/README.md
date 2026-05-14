# 3D Placer — Web Control System

A complete web-based control system for a 4-axis pick-and-place machine driven by an Arduino Mega.

## ✨ Features

- 📡 **Live monitoring** — real-time X/Y/Z/R position display
- 🗺️ **XY trail map** — visual movement history
- 📷 **USB webcam feed** — live camera view with overlay
- 📂 **KiCad .pos import** — drag & drop component placement files
- ⚙️ **Persistent config** — feeders, camera, PCB origin saved to disk
- 🎯 **Automated placement** — full pick & place workflow with progress tracking
- ⏯️ **Run controls** — Start / Pause / Skip / Stop
- ⌨️ **Manual jog & commands** — send G-code-like commands directly

---

## 📁 Project structure

```
3d-placer-monitor/
├── server.js            ← Node.js backend (serial bridge + placement engine)
├── parser.js            ← KiCad .pos file parser
├── placement.js         ← Placement state machine
├── package.json         ← Dependencies
├── config.json          ← Auto-generated machine config (created at runtime)
├── placer_4axis.ino     ← Arduino sketch — 4-axis controller (production)
├── stepper_test.ino     ← Arduino sketch — single stepper test
└── public/
    ├── index.html       ← Monitor dashboard (live data + camera)
    └── placement.html   ← Placement dashboard (PCB workflow)
```

---

## 🔧 Setup

### 1. Install Node.js
Download from https://nodejs.org (v18 or newer)

### 2. Install dependencies
```bash
cd 3d-placer-monitor
npm install
```

### 3. Install AccelStepper library (Arduino IDE)
Arduino IDE → Sketch → Include Library → Manage Libraries → search **"AccelStepper"** → Install

### 4. Upload the Arduino sketch
Open `placer_4axis.ino` in Arduino IDE → Select **Arduino Mega 2560** → Upload.

**Important:** Calibrate the `STEPS_PER_MM_*` constants near the top of the sketch for your specific machine before running on real hardware.

### 5. Start the web server
```bash
npm start
```

### 6. Open the dashboard
- Monitor:   http://localhost:3000
- Placement: http://localhost:3000/placement.html

---

## 🛠️ Hardware wiring (default pins)

| Function   | Arduino Mega pin |
|------------|------------------|
| X STEP     | 2  |
| X DIR      | 3  |
| Y STEP     | 4  |
| Y DIR      | 5  |
| Z STEP     | 6  |
| Z DIR      | 7  |
| R STEP     | 8  |
| R DIR      | 9  |
| ENABLE     | 10 (shared) |
| VACUUM     | 11 (relay/MOSFET) |

Change these in `placer_4axis.ino` if needed.

---

## 🎯 Placement workflow

1. **Calibrate** — jog the head to each feeder, the camera, and the PCB origin. Note all positions.
2. **Configure** — open `/placement.html`, enter all fixed positions, click **SAVE CONFIG**
3. **Export from KiCad** — File → Fabrication Outputs → Component Placement (.pos)
4. **Upload** — drag the `.pos` file onto the upload zone
5. **Map feeders** — assign an X/Y to each component value in the feeder list
6. **Start** — click ▶ START. The machine runs through every component automatically.

---

## 📡 Arduino command protocol

Every command returns `OK` or `ERR: <reason>` when complete.

| Command | Description |
|---|---|
| `MOVE X<n> Y<n> Z<n> R<n>` | Absolute move (any subset of axes) |
| `PICK`     | Lower Z, vacuum ON, raise Z |
| `PLACE`    | Lower Z, vacuum OFF, raise Z |
| `VAC ON` / `VAC OFF` | Manual vacuum control |
| `HOME`     | Reset all position counters to 0 |
| `STATUS`   | Report current X Y Z R |
| `STOP`     | Stop immediately |
| `SPEED <n>` | Set travel speed (mm/s) |

---

## ⚠️ Safety notes

- **Never open Arduino IDE Serial Monitor while the server is running** — they compete for the COM port
- **Set the A4988/DRV8825 current limit** before powering motors — start at 0.5–1A
- **Add 100µF capacitor** across VMOT and GND near each driver
- **Never connect/disconnect motors with the driver powered** — it kills the driver
- **Test moves at low speed first** (`SPEED 20`) before increasing
- **Always home the machine** at startup so position is known

---

## 🐛 Troubleshooting

| Problem | Fix |
|---|---|
| "Cannot open COM port" | Close Arduino IDE Serial Monitor |
| Motor moves wrong distance | Adjust `STEPS_PER_MM_*` in the .ino |
| Motor skips/stalls | Lower speed via `SPEED` command or raise driver current |
| Dashboard says "DISCONNECTED" | Click the COM port selector → CONNECT |
| Component placed in wrong spot | Verify PCB origin coords and PCB rotation in config |
| All components offset by same amount | PCB origin is wrong — re-measure |
