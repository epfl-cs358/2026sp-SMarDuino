# 🤖 SMarDuino : Pick and Place Machine

<p align="center">
  <img src="https://img.shields.io/badge/Status-Project%20Proposal-blue?style=for-the-badge" alt="Status Badge">
  <img src="https://img.shields.io/badge/Hardware-Prusa%20i3%20MK3S%2B-orange?style=for-the-badge" alt="Hardware Badge">
  <img src="https://img.shields.io/badge/License-MIT-green?style=for-the-badge" alt="License Badge">
</p>

---

## 👥 Authors
*   [**Alae Messaoudi**](https://github.com/alaemessaoudi)
*   [**Khalil Romdhane**](https://github.com/khalilromdhane)
*   [**Yahya Boussaadia**](https://github.com/yahyaboussaadia)
*   [**Yosr Hallab**](https://github.com/yosrhallab)
*   [**Zeineb Sellami**](https://github.com/zeinebsellami) 

---

## 📌 Table of Contents
1.  [About The Project](#-about-the-project)
2.  [Final Result](#-final-result)
3.  [Built With](#-built-with)
4.  [Prerequisites & BOM](#-prerequisites--bom)
5.  [Hardware Architecture](#-hardware-architecture)
6.  [Electronics & Power](#-electronics--power)
7.  [Software Layers](#-software-layers)
8.  [Key Challenges & Solutions](#-key-challenges--solutions)
9.  [Milestones](#-milestones)
10. [License](#-license)

---

## 📖 About The Project

### Motivation
Assembling electronic boards manually is a slow and error-prone task. Professional **Pick and Place** machines are often expensive or limited in the thickness of components they can handle.

**SMarDuino** transforms a standard **Prusa i3 MK3S+ 3D printer** into a high-precision automated assembly station. By repurposing the printer's rigid frame and precise motion system, we create a machine capable of:
*   Picking ultra-small SMD components.
*   Correcting orientation via a **Bottom Camera (OpenCV)**.
*   Placing them accurately on a PCB using a vacuum-controlled nozzle.

> *"With SMarDuino, we solve the thickness limitation of industrial machines while keeping the project open-source and affordable."*

( [Back to top](#-table-of-contents) )

---

## 🚀 Final Result
At the end of this semester, SMarDuino functions as an autonomous Cartesian.

### 🎥 Demo
(//we need to add the video of the demo when it is done )
![Demo GIF](https://via.placeholder.com/600x300.png?text=Place+Your+Demo+GIF+Here)
*Watch our full demo video here: [Project Video Link]*

### 📸 Reference Photos
(add photo references)

( [Back to top](#-table-of-contents) )

---

## 🛠 Built With
*   ![React](https://img.shields.io/badge/React-20232A?style=flat&logo=react&logoColor=61DAFB) **Frontend** (Dashboard)
*   ![Python](https://img.shields.io/badge/Python-3776AB?style=flat&logo=python&logoColor=white) **Backend** (Flask & OpenCV)
*   ![Arduino](https://img.shields.io/badge/Arduino-00979D?style=flat&logo=arduino&logoColor=white) **Firmware** (C++/G-Code)
*   ![Prusa](https://img.shields.io/badge/Prusa-FF6600?style=flat&logo=prusa&logoColor=white) **Hardware** (MK3S+ Frame)

---

## 📦 Prerequisites
## Prerequisites
Comprehensive list of elements we used in our project along with needed equipment. To construct the device, one should

**buy:**
*   **Arduino Mega 2560** Board
*   **cnc** Shield
*   5 Stepper motors **17HS4401** + **A4988 Drivers**
*   **Air pump RF370**
*   Air pump tubes (interior diameter 2.5mm and 4mm)
*   5 Air pump bearings (6mm inner)
*   **USB bottom camera**
*   **USB isolator**
*   Power supply **12V 10A**
*   Power supply **12V 5A**
*   2m **Purecrea GT2 belt** (6mm)
*   2 DC Jack
*   M3 and M4 Screws and nuts
*   Lightening rings

**have access to:**
*   **Prusa i3 MK3S+** 3D printer (to be modified)
*   3D printer with **PETG filament** (for custom parts)
*   Laser cutting machine
*   **MDF board** (for the base plate)
*   Soldering kit
*   Bunch of different screwdrivers
*   Driller

( [Back to top](#-table-of-contents) )

---

## 🏗 Hardware Architecture


#### 1. Mechanical Conversion
We replaced the standard Prusa extruder with a custom-engineered **X-Carriage** designed specifically for Pick and Place operations. This modular head integrates both the component handling and the feeding trigger.

*   **Vacuum Grabber Mechanism**: 
    *   Features a high-precision **vacuum nozzle** mounted on a spring-loaded Z-axis.
    *   Integrated with a **Y-junction pneumatic circuit** to allow for both picking and release.

#### 2. Passive Tape Feeding System
Inspired by the **LumenPnP** open-source community, our feeding system is **fully mechanical and passive**, significantly reducing the machine's weight and electronic footprint.

*   **Nozzle-Driven Advance**: 
    *   The head moves to the feeder, and the vacuum nozzle descends into the tape's **sprocket holes**.
    *   The machine performs a precise linear move to pull the tape forward, eliminating the need for individual stepper motors or servos for each feeder.
*   **Automatic Synchronized Peeling**: 
    *   This linear pull drives a series of **3D-printed internal gears**.
    *   The gears are calibrated with a specific ratio to peel back the protective film automatically as the tape advances, ensuring the components are always exposed at the exact pick-up location.
*   **Design Efficiency**: 
    *   **Scalable**: New feeders can be added to the base plate without needing additional motor drivers or wiring.

---

## ⚡ Electronics & Power
To avoid electrical noise, we implemented a **Dual Power Supply** system:
1.  **PSU A (12V/10A)**: Dedicated to the 5 high-current stepper motors.


## Electronics Assembly

The final electronic architecture of the project has been assembled.

- Arduino Mega connected to the CNC Shield
- Five stepper motor drivers installed and configured
- X, Y, Z and rotation motors connected
- Limit switches added for homing
- Vacuum pump controlled using a transistor switching circuit
- The transistor allows the Arduino to safely activate and deactivate the pump
- Buck converter used to provide a stable power supply for the pump
- Camera system connected for component alignment
- Common ground shared between all electronic modules

This setup allows the machine to perform the full pick-and-place workflow:
homing, component pickup, camera alignment, and PCB placement.
**Circuit Schematic:**
![Electronics Diagram]()
(<img width="1451" height="770" alt="Capture d&#39;écran 2026-05-21 030021" src="https://github.com/user-attachments/assets/d6a49caf-2faa-4bd9-badd-0593c9f2a8b9" />


( [Back to top](#-table-of-contents) )

---

## 💻 Software Layers
SMarDuino runs on a three-tier software architecture:

*   **Layer 1 (Firmware)**: Custom G-Code interpreter on Arduino Mega to handle XYZ movements and vacuum solenoid.
*   **Layer 2 (Vision)**: Python script using **OpenCV** to detect component contours, extract rotation angles, and send correction commands.
*   **Layer 3 (UI)**: A web dashboard to upload PCB design files (CSV) and monitor the placement progress in real-time.

---

## ⚠️ Key Challenges & Solutions

| Problem | Solution |
| :--- | :--- |
| **Sticky Components** | Implemented a **Y-junction pneumatic circuit** with a "Blow-off" pulse to cleanly release parts. |
| **Tube Twisting** | Integrated **Sealing Bearings** and software rotation limits (-90° to +90°). |
| **Precision** | Leveraged the MK3S+ micro-stepping capabilities for high repeatability. |

---

## 📅 Milestones
*   [x] **Milestone 1**: Mechanical conversion and basic XYZ motion control.
*   [x] **Milestone 2**: Integration of the feeding system and OpenCV vision module.
*   [ ] **Milestone 3**: Final reliability testing and full autonomous PCB assembly.

---

## Software
We are building a Next.js App for the interface and a Flask server for machine control.

### Frontend
Written in TypeScript, it allows the user to upload CSV files containing component coordinates and monitor the placement progress.

