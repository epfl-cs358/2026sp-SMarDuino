/*
 * 3D Placer — 4-Axis Controller (webapp protocol)
 * Hardware: Arduino Mega + CNC Shield + 4× stepper drivers + vacuum pump
 *
 * Motion logic is identical to the reference sketch:
 *   - Uses motor.move(steps) for RELATIVE moves (not moveTo)
 *   - Same limit switch protection (runMotorWithLimit)
 *   - Same homing logic (homeAxis with constant-speed approach)
 *   - Same position-reporting format
 *
 * IMPORTANT — Install library first:
 *   Arduino IDE → Sketch → Include Library → Manage Libraries
 *   → Search "AccelStepper" → Install
 *
 * Protocol — every command returns "OK" or "ERR: <reason>" when complete.
 * The web app waits for that response before sending the next command.
 *
 *   MOVE X<val> Y<val> Z<val> R<val>   ABSOLUTE move (go to position X=val)
 *   STEP X<val> Y<val> Z<val> R<val>   RELATIVE move (move val steps from current)
 *   ADVANCE Y<pitch> Z<travelZ>         Tape advance from pickup position:
 *                                       Nozzle starts at pickup Z in pocket. Sequence:
 *                                         0. plunge +100 deeper to grip tape hole
 *                                         1. drag Y by -<pitch>
 *                                         2. lift Z to <travelZ>
 *                                         3. return Y by +<pitch>
 *                                         4. plunge back to deep position
 *                                       Ends with nozzle deep in pocket; caller
 *                                       should lift to travel Z after this.
 *                                       Example: ADVANCE Y430 Z5000
 *   PICK                                lower Z, vacuum ON, raise Z
 *   PLACE                               lower Z, vacuum OFF, raise Z
 *   VAC ON | VAC OFF | ON | OFF         manual vacuum control
 *   HOME                                home all axes with limit switches
 *   HOME X | HOME Y | HOME Z            home one axis
 *   ZERO                                set current position as 0 for all motors
 *   STATUS                              report current X Y Z R
 *   STOP                                immediate stop
 *   SWITCHES                            print limit switch states
 */

#include <AccelStepper.h>

// ─── PIN CONFIG (CNC Shield V3) ──────────────────────────────────────────
#define ENABLE_PIN     8
#define X_STEP_PIN     2
#define X_DIR_PIN      5
#define Y_STEP_PIN     3
#define Y_DIR_PIN      6
#define Z_STEP_PIN     4
#define Z_DIR_PIN      7
#define ROT_STEP_PIN   18
#define ROT_DIR_PIN    17
#define ROT_ENABLE_PIN 19
#define PUMP_PIN       A0
#define X_LIMIT_PIN    14
#define Y_LIMIT_PIN    15
#define Z_LIMIT_PIN    16

AccelStepper motorX(AccelStepper::DRIVER, X_STEP_PIN, X_DIR_PIN);
AccelStepper motorY(AccelStepper::DRIVER, Y_STEP_PIN, Y_DIR_PIN);
AccelStepper motorZ(AccelStepper::DRIVER, Z_STEP_PIN, Z_DIR_PIN);
AccelStepper motorR(AccelStepper::DRIVER, ROT_STEP_PIN, ROT_DIR_PIN);

// ─── HOMING CONFIG ───────────────────────────────────────────────────────
// X moves positive, Y negative, Z negative
const float X_HOMING_SPEED =  800;
const float Y_HOMING_SPEED = -800;
const float Z_HOMING_SPEED = -1500;

// Which side of the workspace is each switch on?
// true  = switch on positive end of travel
// false = switch on negative end of travel
const bool X_LIMIT_IS_POSITIVE = true;
const bool Y_LIMIT_IS_POSITIVE = false;
const bool Z_LIMIT_IS_POSITIVE = false;

// NC switches + INPUT_PULLUP:
//   not pressed → pin reads LOW
//   pressed     → pin reads HIGH
byte xLimitCounter = 0;
byte yLimitCounter = 0;
byte zLimitCounter = 0;
const byte LIMIT_DEBOUNCE_COUNT = 5;

// ─── PICK / PLACE CONFIG ─────────────────────────────────────────────────
// Relative Z moves for pick/place sequences.
// Negative = down, positive = up.
// You can tune these or override via config from the webapp.
long Z_DROP_STEPS = -500;   // how far DOWN to drop nozzle for pick/place
long Z_LIFT_STEPS =  500;   // how far UP to lift after (should equal -Z_DROP_STEPS)

// ─── STATE ───────────────────────────────────────────────────────────────
String inputBuffer = "";
unsigned long lastReport = 0;
const unsigned long REPORT_INTERVAL = 200;  // ms

// ─────────────────────────────────────────────────────────────────────────
void setup() {
  Serial.begin(9600);
  delay(2000);  // let serial stabilize after DTR reset

  // Enable CNC Shield drivers
  pinMode(ENABLE_PIN, OUTPUT);
  digitalWrite(ENABLE_PIN, LOW);

  // Enable rotation motor driver
  pinMode(ROT_ENABLE_PIN, OUTPUT);
  digitalWrite(ROT_ENABLE_PIN, LOW);

  // Pump
  pinMode(PUMP_PIN, OUTPUT);
  digitalWrite(PUMP_PIN, LOW);

  // Limit switches
  pinMode(X_LIMIT_PIN, INPUT_PULLUP);
  pinMode(Y_LIMIT_PIN, INPUT_PULLUP);
  pinMode(Z_LIMIT_PIN, INPUT_PULLUP);

  // Motor speeds & acceleration (same as reference sketch)
  motorX.setMaxSpeed(1000);
  motorX.setAcceleration(3000);

  motorY.setMaxSpeed(2000);
  motorY.setAcceleration(4000);

  motorZ.setMaxSpeed(2000);
  motorZ.setAcceleration(3000);

  motorR.setMaxSpeed(2000);
  motorR.setAcceleration(3000);

  Serial.println("READY");
  Serial.println("4-axis placer online.");
}

// ─────────────────────────────────────────────────────────────────────────
void loop() {
  readSerial();

  // X, Y, Z get limit-switch protection
  runMotorWithLimit(motorX, X_LIMIT_PIN, X_LIMIT_IS_POSITIVE, "X", xLimitCounter);
  runMotorWithLimit(motorY, Y_LIMIT_PIN, Y_LIMIT_IS_POSITIVE, "Y", yLimitCounter);
  runMotorWithLimit(motorZ, Z_LIMIT_PIN, Z_LIMIT_IS_POSITIVE, "Z", zLimitCounter);

  // R has no limit switch
  motorR.run();

  // Periodic position report
  if (millis() - lastReport > REPORT_INTERVAL) {
    reportPosition();
    lastReport = millis();
  }
}

// ─── SERIAL READ ─────────────────────────────────────────────────────────
void readSerial() {
  while (Serial.available()) {
    char c = Serial.read();
    if (c == '\n' || c == '\r') {
      if (inputBuffer.length() > 0) {
        handleCommand(inputBuffer);
        inputBuffer = "";
      }
    } else {
      inputBuffer += c;
    }
  }
}

// ─── COMMAND HANDLER ─────────────────────────────────────────────────────
void handleCommand(String cmd) {
  cmd.trim();
  String upper = cmd; upper.toUpperCase();

  if (upper.startsWith("MOVE")) {
    handleMove(cmd, false);   // absolute
  }
  else if (upper.startsWith("STEP")) {
    handleMove(cmd, true);    // relative
  }
  else if (upper.startsWith("ADVANCE")) {
    handleAdvance(cmd);
  }
  else if (upper == "PICK") {
    doPick();
    Serial.println("OK");
  }
  else if (upper == "PLACE") {
    doPlace();
    Serial.println("OK");
  }
  else if (upper == "VAC ON" || upper == "ON") {
    digitalWrite(PUMP_PIN, HIGH);
    Serial.println("OK");
  }
  else if (upper == "VAC OFF" || upper == "OFF") {
    digitalWrite(PUMP_PIN, LOW);
    Serial.println("OK");
  }
  else if (upper == "HOME") {
    doHoming();
    Serial.println("OK");
  }
  else if (upper == "HOME X") {
    homeAxis(motorX, X_LIMIT_PIN, X_HOMING_SPEED, "X");
    Serial.println("OK");
  }
  else if (upper == "HOME Y") {
    homeAxis(motorY, Y_LIMIT_PIN, Y_HOMING_SPEED, "Y");
    Serial.println("OK");
  }
  else if (upper == "HOME Z") {
    homeAxis(motorZ, Z_LIMIT_PIN, Z_HOMING_SPEED, "Z");
    Serial.println("OK");
  }
  else if (upper == "ZERO") {
    motorX.setCurrentPosition(0);
    motorY.setCurrentPosition(0);
    motorZ.setCurrentPosition(0);
    motorR.setCurrentPosition(0);
    Serial.println("OK");
  }
  else if (upper == "STATUS") {
    reportPosition();
    Serial.println("OK");
  }
  else if (upper == "STOP") {
    motorX.stop(); motorY.stop(); motorZ.stop(); motorR.stop();
    Serial.println("OK");
  }
  else if (upper == "SWITCHES") {
    printSwitches();
    Serial.println("OK");
  }
  else {
    Serial.print("ERR: unknown command: "); Serial.println(cmd);
  }
}

// ─── MOVE / STEP COMMAND ─────────────────────────────────────────────────
// MOVE X1000 Y500 Z-200 R45    → ABSOLUTE: motorX.moveTo(1000)
// STEP X1000 Y500 Z-200 R45    → RELATIVE: motorX.move(1000)
// All specified axes move simultaneously. Waits until all have arrived.
void handleMove(String cmd, bool relative) {
  long vx = 0, vy = 0, vz = 0, vr = 0;
  bool hasX = false, hasY = false, hasZ = false, hasR = false;

  int idx;
  if ((idx = cmd.indexOf('X')) >= 0) { vx = cmd.substring(idx+1).toInt(); hasX = true; }
  if ((idx = cmd.indexOf('Y')) >= 0) { vy = cmd.substring(idx+1).toInt(); hasY = true; }
  if ((idx = cmd.indexOf('Z')) >= 0) { vz = cmd.substring(idx+1).toInt(); hasZ = true; }
  if ((idx = cmd.indexOf('R')) >= 0) { vr = cmd.substring(idx+1).toInt(); hasR = true; }

  if (relative) {
    // STEP: relative moves (matches reference sketch's motor.move())
    if (hasX) motorX.move(vx);
    if (hasY) motorY.move(vy);
    if (hasZ) motorZ.move(vz);
    if (hasR) motorR.move(vr);
  } else {
    // MOVE: absolute targets
    if (hasX) motorX.moveTo(vx);
    if (hasY) motorY.moveTo(vy);
    if (hasZ) motorZ.moveTo(vz);
    if (hasR) motorR.moveTo(vr);
  }

  // Run motors until all arrive (with limit protection on X/Y/Z)
  while (motorX.distanceToGo() != 0 || motorY.distanceToGo() != 0 ||
         motorZ.distanceToGo() != 0 || motorR.distanceToGo() != 0) {
    runMotorWithLimit(motorX, X_LIMIT_PIN, X_LIMIT_IS_POSITIVE, "X", xLimitCounter);
    runMotorWithLimit(motorY, Y_LIMIT_PIN, Y_LIMIT_IS_POSITIVE, "Y", yLimitCounter);
    runMotorWithLimit(motorZ, Z_LIMIT_PIN, Z_LIMIT_IS_POSITIVE, "Z", zLimitCounter);
    motorR.run();

    if (millis() - lastReport > REPORT_INTERVAL) {
      reportPosition();
      lastReport = millis();
    }
  }

  Serial.println("OK");
}

// ─── ADVANCE COMMAND (tape feeder scroll, returns to pocket) ─────────────
// ADVANCE Y<pitch> Z<travelZ>
//   Starting condition: nozzle is DOWN in the feeder pocket (at pocket Z).
//   Sequence:
//     0. Plunge an EXTRA 100 steps deeper into the pocket (grip the tape hole)
//     1. Drag Y by -<pitch>       (scroll the tape backward while nozzle grips)
//     2. Lift Z to <travelZ>      (lift the nozzle out)
//     3. Return Y by +<pitch>     (head back to original Y over pocket)
//     4. Plunge Z back to deep    (return to the deeper position)
//   Ending condition: nozzle is DOWN in pocket (at original + 100 deeper).
//   Caller should lift to travel Z afterward.
// Example: ADVANCE Y430 Z5000
void handleAdvance(String cmd) {
  long pitch = 0;
  long travelZ = 0;
  bool hasY = false, hasZ = false;

  int idx;
  if ((idx = cmd.indexOf('Y')) >= 0) { pitch = cmd.substring(idx+1).toInt(); hasY = true; }
  if ((idx = cmd.indexOf('Z')) >= 0) { travelZ = cmd.substring(idx+1).toInt(); hasZ = true; }

  if (!hasY || pitch == 0) {
    Serial.println("ERR: ADVANCE requires Y<pitch>");
    return;
  }
  if (!hasZ) {
    Serial.println("ERR: ADVANCE requires Z<travelZ>");
    return;
  }

  // Remember where we started (nozzle is currently in the pocket at pickup Z)
  long startZ = motorZ.currentPosition();

  // Step 0: plunge 100 deeper to grip the tape hole
  // Z+ is DOWN on this machine
  long deepZ = startZ + 100;
  motorZ.moveTo(deepZ);
  while (motorZ.distanceToGo() != 0) {
    runMotorWithLimit(motorZ, Z_LIMIT_PIN, Z_LIMIT_IS_POSITIVE, "Z", zLimitCounter);
  }

  // Step 1: drag Y by -pitch (scroll tape backward, nozzle gripping hole)
  motorY.move(-pitch);
  while (motorY.distanceToGo() != 0) {
    runMotorWithLimit(motorY, Y_LIMIT_PIN, Y_LIMIT_IS_POSITIVE, "Y", yLimitCounter);
  }

  // Step 2: lift Z to travel height (absolute)
  motorZ.moveTo(travelZ);
  while (motorZ.distanceToGo() != 0) {
    runMotorWithLimit(motorZ, Z_LIMIT_PIN, Z_LIMIT_IS_POSITIVE, "Z", zLimitCounter);
  }

  // Step 3: return Y by +pitch (head back over original pocket)
  motorY.move(pitch);
  while (motorY.distanceToGo() != 0) {
    runMotorWithLimit(motorY, Y_LIMIT_PIN, Y_LIMIT_IS_POSITIVE, "Y", yLimitCounter);
  }

  // Step 4: plunge back to deep position (startZ + 100)
  motorZ.moveTo(deepZ);
  while (motorZ.distanceToGo() != 0) {
    runMotorWithLimit(motorZ, Z_LIMIT_PIN, Z_LIMIT_IS_POSITIVE, "Z", zLimitCounter);
  }

  Serial.println("OK");
}
void doPick() {
  // Drop Z, vacuum ON, raise Z
  motorZ.move(Z_DROP_STEPS);
  while (motorZ.distanceToGo() != 0) {
    runMotorWithLimit(motorZ, Z_LIMIT_PIN, Z_LIMIT_IS_POSITIVE, "Z", zLimitCounter);
  }
  digitalWrite(PUMP_PIN, HIGH);
  delay(200);
  motorZ.move(Z_LIFT_STEPS);
  while (motorZ.distanceToGo() != 0) {
    runMotorWithLimit(motorZ, Z_LIMIT_PIN, Z_LIMIT_IS_POSITIVE, "Z", zLimitCounter);
  }
}

void doPlace() {
  motorZ.move(Z_DROP_STEPS);
  while (motorZ.distanceToGo() != 0) {
    runMotorWithLimit(motorZ, Z_LIMIT_PIN, Z_LIMIT_IS_POSITIVE, "Z", zLimitCounter);
  }
  digitalWrite(PUMP_PIN, LOW);
  delay(200);
  motorZ.move(Z_LIFT_STEPS);
  while (motorZ.distanceToGo() != 0) {
    runMotorWithLimit(motorZ, Z_LIMIT_PIN, Z_LIMIT_IS_POSITIVE, "Z", zLimitCounter);
  }
}

// ─── HOMING (identical to reference sketch) ──────────────────────────────
void doHoming() {
  homeAxis(motorX, X_LIMIT_PIN, X_HOMING_SPEED, "X");
  delay(500);
  homeAxis(motorY, Y_LIMIT_PIN, Y_HOMING_SPEED, "Y");
  delay(500);
  homeAxis(motorZ, Z_LIMIT_PIN, Z_HOMING_SPEED, "Z");
  delay(500);
  motorR.setCurrentPosition(0);
}

void homeAxis(AccelStepper &motor, int limitPin, float speed, const char* axisName) {
  Serial.print("Homing ");
  Serial.print(axisName);
  Serial.println(" axis...");

  motor.setSpeed(speed);

  // NC + INPUT_PULLUP: not pressed = LOW, pressed = HIGH
  while (digitalRead(limitPin) == LOW) {
    motor.runSpeed();
  }

  motor.setSpeed(0);
  motor.setCurrentPosition(0);

  Serial.print(axisName);
  Serial.println(" axis homed.");
}

// ─── LIMIT-PROTECTED RUN (identical to reference sketch) ─────────────────
void runMotorWithLimit(
  AccelStepper &motor, int limitPin, bool limitIsPositive,
  const char* axisName, byte &limitCounter
) {
  long distance = motor.distanceToGo();
  bool movingPositive = distance > 0;
  bool movingNegative = distance < 0;

  bool movingTowardLimit =
    (limitIsPositive && movingPositive) ||
    (!limitIsPositive && movingNegative);

  if (movingTowardLimit) {
    bool switchPressed = digitalRead(limitPin) == HIGH;
    if (switchPressed) {
      if (limitCounter < LIMIT_DEBOUNCE_COUNT) limitCounter++;
    } else {
      limitCounter = 0;
    }
    if (limitCounter >= LIMIT_DEBOUNCE_COUNT) {
      long current = motor.currentPosition();
      motor.moveTo(current);
      motor.setSpeed(0);
      Serial.print("LIMIT HIT on ");
      Serial.print(axisName);
      Serial.println(" axis. Motor stopped.");
      limitCounter = 0;
      return;
    }
  } else {
    limitCounter = 0;
  }

  motor.run();
}

// ─── REPORTING ───────────────────────────────────────────────────────────
void reportPosition() {
  Serial.print("X:");  Serial.print(motorX.currentPosition());
  Serial.print(" Y:"); Serial.print(motorY.currentPosition());
  Serial.print(" Z:"); Serial.print(motorZ.currentPosition());
  Serial.print(" R:"); Serial.println(motorR.currentPosition());
}

void printSwitches() {
  Serial.print("X limit: "); Serial.print(digitalRead(X_LIMIT_PIN));
  Serial.print(" | Y limit: "); Serial.print(digitalRead(Y_LIMIT_PIN));
  Serial.print(" | Z limit: "); Serial.println(digitalRead(Z_LIMIT_PIN));
  Serial.println("With NC + INPUT_PULLUP: 0 = not pressed, 1 = pressed/open");
}
