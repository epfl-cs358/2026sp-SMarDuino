# 3D Placer — Web Control System

Web-based monitor & control for a 4-axis (X, Y, Z, R) pick-and-place machine using a CNC Shield + Arduino Mega.

## ✨ Features

- 📡 **Live monitoring** — X / Y / Z / R positions in real time
- 🗺️ **XY trail map** — visual movement history
- 📷 **USB webcam feed**
- 📂 **KiCad .pos import** — drag & drop component placement files
- ⚙️ **Capture buttons** — jog the head, click ⊕ to grab the current position
- ⚙️ **Persistent config** — feeders, camera, PCB origin saved to disk
- 🎯 **Automated placement** — full pick & place workflow
- ⏯️ **Run controls** — Start / Pause / Skip / Stop
- 🔒 **Limit switch homing** — real homing with NC switches
- ⌨️ **Manual commands** — send any command directly to the Arduino

---

## 🔌 Hardware

**Board:** Arduino Mega 2560 + CNC Shield V3

| Function | Pin | Notes |
|---|---|---|
| X STEP / DIR | 2 / 5 | CNC Shield X slot |
| Y STEP / DIR | 3 / 6 | CNC Shield Y slot |
| Z STEP / DIR | 4 / 7 | CNC Shield Z slot |
| R STEP / DIR | 18 / 17 | Separate driver (rotation) |
| R ENABLE | 19 | Dedicated enable for rotation driver |
| ENABLE (X/Y/Z) | 8 | Shared CNC Shield enable |
| Vacuum pump | A0 | Via relay or MOSFET |
| X limit switch | 14 | NC, wired to GND, INPUT_PULLUP |
| Y limit switch | 15 | NC |
| Z limit switch | 16 | NC |

**Homing directions:** X positive, Y negative, Z negative

---

## 🛠️ Setup

### 1. Install Node.js
https://nodejs.org (v18+)

### 2. Install dependencies
```bash
cd 3d-placer-monitor
npm install
```

### 3. Install AccelStepper library
Arduino IDE → Sketch → Include Library → Manage Libraries → search **"AccelStepper"** → Install

### 4. Upload the Arduino sketch
Open `placer_4axis.ino` → select **Arduino Mega 2560** → Upload.

### 5. Start the web server
```bash
npm start
```

### 6. Open the dashboards
- Monitor:   http://localhost:3000
- Placement: http://localhost:3000/placement.html

---

## 📡 Arduino command protocol

Every command returns `OK` or `ERR: <reason>` when complete.

| Command | Description |
|---|---|
| `MOVE X<n> Y<n> Z<n> R<n>` | Absolute move (any subset of axes) |
| `PICK` | Lower Z to Z_PICK, vacuum ON, raise Z |
| `PLACE` | Lower Z to Z_PLACE, vacuum OFF, raise Z |
| `VAC ON` / `VAC OFF` | Manual vacuum control |
| `HOME` | Home all axes (Z first for safety) |
| `HOME X` / `HOME Y` / `HOME Z` | Home one axis |
| `STATUS` | Report current X Y Z R |
| `STOP` | Immediate stop |
| `SPEED <n>` | Set max speed in steps/sec |

**All coordinates are in STEPS, not mm.** What you send is what the motor does.

---

## 🎯 Placement workflow

1. **Home the machine** — send `HOME` (uses limit switches)
2. **Jog the head** to each fixed position using the JOG panel
3. **Click ⊕ CAPTURE** to grab the current position into the config:
   - PCB origin (bottom-left corner of where the PCB will sit)
   - Camera position
   - Z Travel (safe height to fly between points)
   - Z Pick (touching the feeder)
   - Z Place (touching the PCB)
   - Each feeder XY (one ⊕ per feeder row)
4. **Set Steps per mm** — calibrate so .pos mm coords convert correctly
5. **Click SAVE CONFIG**
6. **Export .pos from KiCad** → File → Fabrication Outputs → Component Placement
7. **Drag the .pos file** onto the upload zone in the placement dashboard
8. **Click ▶ START** — the machine runs through every component

---

## 📏 Calibrating "steps per mm"

The .pos file uses mm. Your machine uses steps. The conversion factor:

1. From the dashboard, send `HOME` then `MOVE X1000`
2. The head moves 1000 steps. **Measure the distance in mm.**
3. Compute: `steps_per_mm = 1000 / mm_moved`
4. Enter this value in the SETUP panel → SAVE CONFIG

Typical values:
- GT2 belt + 20T pulley + 1/16 microstep: ~80
- Leadscrew M5 + 1/16 microstep: ~400

---

## 🔒 Limit switch homing

The sketch implements real homing using your NC limit switches:
- **Each axis drives at a constant speed** toward its switch
- **The motor stops the instant the switch triggers** (NC switch opens when pressed → pin reads HIGH)
- **Current position is set to 0** at the switch
- **During normal moves**, the same switches act as safety: if the motor is moving *toward* its switch and the switch triggers, the motor stops immediately (with debouncing to avoid false triggers)

---

## ⚠️ Safety notes

- **Close Arduino IDE Serial Monitor** before running `npm start` (only one program can use a COM port)
- **Set driver current limits** before powering motors (~0.5-1A to start)
- **Add 100µF capacitor** across VMOT/GND near each driver
- **Test moves at low speed first**
- **Always `HOME`** at startup so position is known

---

## 🐛 Troubleshooting

| Problem | Fix |
|---|---|
| "Cannot open COM port" | Close Arduino IDE Serial Monitor |
| Z homes but doesn't stop at switch | Wrong `Z_LIMIT_IS_POSITIVE` value in the sketch |
| Motor moves wrong direction in homing | Flip sign of `_HOMING_SPEED` constant |
| All axes drift over time | Driver current too low — increase trim pot voltage |
| Component placed wrong spot | Re-capture PCB origin / verify rotation |
