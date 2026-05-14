#include <AccelStepper.h>

// =============================================================
//  4-axis placer controller for ARDUINO MEGA
//  Based on your working Serial Monitor code
//
//  Supports:
//    - Old step commands: X 200, Y -100, Z 50, A 50, R 400, ALL 100
//    - Web app commands: STATUS, ZERO, HOME, HOME X, MOVE X10 Y5 Z8 R0,
//                        SPEED 5, VAC ON, VAC OFF, PICK, PLACE, STOP
//    - Position printing only when position changes
// =============================================================

// -------------------- Pins --------------------
#define ENABLE_PIN 8

// CNC Shield axes
#define X_STEP_PIN 2
#define X_DIR_PIN  5

#define Y_STEP_PIN 3
#define Y_DIR_PIN  6

#define Z_STEP_PIN 4
#define Z_DIR_PIN  7

#define A_STEP_PIN 12
#define A_DIR_PIN  13

// Rotation motor
#define ROT_DIR_PIN    17
#define ROT_STEP_PIN   18
#define ROT_ENABLE_PIN 19

// Pump transistor
#define PUMP_PIN A0

// Limit switches on Arduino Mega
// Note: On Mega, pins 14/15/16 are normal digital pins too.
// They are NOT A0/A1/A2. A0 is digital pin 54 on Mega.
#define X_LIMIT_PIN 14
#define Y_LIMIT_PIN 15
#define Z_LIMIT_PIN 16

// -------------------- Motors --------------------
AccelStepper motorX(AccelStepper::DRIVER, X_STEP_PIN, X_DIR_PIN);
AccelStepper motorY(AccelStepper::DRIVER, Y_STEP_PIN, Y_DIR_PIN);
AccelStepper motorZ(AccelStepper::DRIVER, Z_STEP_PIN, Z_DIR_PIN);
AccelStepper motorA(AccelStepper::DRIVER, A_STEP_PIN, A_DIR_PIN);
AccelStepper motorR(AccelStepper::DRIVER, ROT_STEP_PIN, ROT_DIR_PIN);

// -------------------- Calibration --------------------
// IMPORTANT: Adjust these values after measuring the real movement.
// Web commands use mm for X/Y/Z and degrees for R.
const float STEPS_PER_MM_X = 80.0;
const float STEPS_PER_MM_Y = 80.0;
const float STEPS_PER_MM_Z = 400.0;
const float STEPS_PER_DEG_R = 10.0;

// -------------------- Safe Z values --------------------
// First tests should stay high. Lower them gradually only after testing.
const float Z_TRAVEL = 10.0;
const float Z_PICK   = 8.0;
const float Z_PLACE  = 8.0;

// -------------------- Homing speeds --------------------
// Same directions as your working homing code:
// X moves positive, Y negative, Z negative.
const float X_HOMING_SPEED = 800.0;
const float Y_HOMING_SPEED = -800.0;
const float Z_HOMING_SPEED = -800.0;

const unsigned long HOMING_TIMEOUT_MS = 15000;

// -------------------- Position printing --------------------
long lastPrintedX = 2147483647L;
long lastPrintedY = 2147483647L;
long lastPrintedZ = 2147483647L;
long lastPrintedA = 2147483647L;
long lastPrintedR = 2147483647L;
int lastPrintedPump = -1;

const unsigned long POSITION_PRINT_INTERVAL_MS = 100;
unsigned long lastPositionPrintMs = 0;

// -------------------- Helpers --------------------
void setupSpeeds(float xySpeedMmPerSec = 20.0) {
  motorX.setMaxSpeed(xySpeedMmPerSec * STEPS_PER_MM_X);
  motorY.setMaxSpeed(xySpeedMmPerSec * STEPS_PER_MM_Y);
  motorZ.setMaxSpeed(10.0 * STEPS_PER_MM_Z);
  motorA.setMaxSpeed(10.0 * STEPS_PER_MM_Z);
  motorR.setMaxSpeed(180.0 * STEPS_PER_DEG_R);

  motorX.setAcceleration(3000);
  motorY.setAcceleration(4000);
  motorZ.setAcceleration(3000);
  motorA.setAcceleration(3000);
  motorR.setAcceleration(3000);
}

void printHelp() {
  Serial.println("READY");
  Serial.println("Arduino Mega 4-axis placer online.");
  Serial.println("Web commands:");
  Serial.println("  STATUS");
  Serial.println("  ZERO");
  Serial.println("  HOME / HOME X / HOME Y / HOME Z");
  Serial.println("  MOVE X10 Y5 Z8 R0");
  Serial.println("  SPEED 5");
  Serial.println("  VAC ON / VAC OFF");
  Serial.println("  PICK / PLACE");
  Serial.println("  STOP");
  Serial.println("Old step commands:");
  Serial.println("  X 200, Y -100, Z 50, A 300, R 400, ALL 100");
  Serial.println("  on / off");
}

float currentXmm() { return motorX.currentPosition() / STEPS_PER_MM_X; }
float currentYmm() { return motorY.currentPosition() / STEPS_PER_MM_Y; }
float currentZmm() { return motorZ.currentPosition() / STEPS_PER_MM_Z; }
float currentAmm() { return motorA.currentPosition() / STEPS_PER_MM_Z; }
float currentRdeg() { return motorR.currentPosition() / STEPS_PER_DEG_R; }

void printPosition() {
  Serial.print("POS X:");
  Serial.print(currentXmm(), 2);
  Serial.print(" Y:");
  Serial.print(currentYmm(), 2);
  Serial.print(" Z:");
  Serial.print(currentZmm(), 2);
  Serial.print(" R:");
  Serial.print(currentRdeg(), 1);
  Serial.print(" A:");
  Serial.print(currentAmm(), 2);
  Serial.print(" Pump:");
  Serial.println(digitalRead(PUMP_PIN) == HIGH ? "ON" : "OFF");
}

void printPositionIfChanged() {
  unsigned long now = millis();
  if (now - lastPositionPrintMs < POSITION_PRINT_INTERVAL_MS) return;

  long x = motorX.currentPosition();
  long y = motorY.currentPosition();
  long z = motorZ.currentPosition();
  long a = motorA.currentPosition();
  long r = motorR.currentPosition();
  int pump = digitalRead(PUMP_PIN);

  bool changed =
    x != lastPrintedX ||
    y != lastPrintedY ||
    z != lastPrintedZ ||
    a != lastPrintedA ||
    r != lastPrintedR ||
    pump != lastPrintedPump;

  if (changed) {
    printPosition();
    lastPrintedX = x;
    lastPrintedY = y;
    lastPrintedZ = z;
    lastPrintedA = a;
    lastPrintedR = r;
    lastPrintedPump = pump;
    lastPositionPrintMs = now;
  }
}

void runAllMotors() {
  motorX.run();
  motorY.run();
  motorZ.run();
  motorA.run();
  motorR.run();
}

bool motorsBusy() {
  return motorX.distanceToGo() != 0 ||
         motorY.distanceToGo() != 0 ||
         motorZ.distanceToGo() != 0 ||
         motorA.distanceToGo() != 0 ||
         motorR.distanceToGo() != 0;
}

void runUntilDone() {
  while (motorsBusy()) {
    runAllMotors();
    printPositionIfChanged();
  }
}

void stopAllMotorsNow() {
  motorX.moveTo(motorX.currentPosition());
  motorY.moveTo(motorY.currentPosition());
  motorZ.moveTo(motorZ.currentPosition());
  motorA.moveTo(motorA.currentPosition());
  motorR.moveTo(motorR.currentPosition());
  Serial.println("OK stopped");
}

void zeroAllAxes() {
  motorX.setCurrentPosition(0);
  motorY.setCurrentPosition(0);
  motorZ.setCurrentPosition(0);
  motorA.setCurrentPosition(0);
  motorR.setCurrentPosition(0);

  lastPrintedX = 2147483647L;
  lastPrintedY = 2147483647L;
  lastPrintedZ = 2147483647L;
  lastPrintedA = 2147483647L;
  lastPrintedR = 2147483647L;

  Serial.println("OK zeroed");
  printPosition();
}

void moveToMm(float x, float y, float z, float r) {
  motorX.moveTo(lround(x * STEPS_PER_MM_X));
  motorY.moveTo(lround(y * STEPS_PER_MM_Y));

  long zSteps = lround(z * STEPS_PER_MM_Z);
  motorZ.moveTo(zSteps);
  motorA.moveTo(zSteps); // A mirrors Z mechanically using inverted direction

  motorR.moveTo(lround(r * STEPS_PER_DEG_R));
}

void moveZToMm(float z) {
  long zSteps = lround(z * STEPS_PER_MM_Z);
  motorZ.moveTo(zSteps);
  motorA.moveTo(zSteps);
}

void vacuumOn() {
  digitalWrite(PUMP_PIN, HIGH);
  Serial.println("OK vacuum on");
}

void vacuumOff() {
  digitalWrite(PUMP_PIN, LOW);
  Serial.println("OK vacuum off");
}

// NC switch with INPUT_PULLUP, as in your homing code:
// not pressed = LOW, pressed/open = HIGH
bool homeSingleAxis(AccelStepper &motor, int limitPin, float speed, const char* axisName) {
  Serial.print("Homing ");
  Serial.print(axisName);
  Serial.println(" axis...");

  unsigned long startTime = millis();
  motor.setSpeed(speed);

  while (digitalRead(limitPin) == LOW) {
    motor.runSpeed();

    if (millis() - startTime > HOMING_TIMEOUT_MS) {
      Serial.print("ERR homing timeout on ");
      Serial.println(axisName);
      return false;
    }
  }

  motor.setSpeed(0);
  motor.setCurrentPosition(0);

  Serial.print("OK ");
  Serial.print(axisName);
  Serial.println(" homed");
  return true;
}

bool homeZAxisWithA() {
  Serial.println("Homing Z axis with mirrored A axis...");

  unsigned long startTime = millis();
  motorZ.setSpeed(Z_HOMING_SPEED);
  motorA.setSpeed(Z_HOMING_SPEED);

  while (digitalRead(Z_LIMIT_PIN) == LOW) {
    motorZ.runSpeed();
    motorA.runSpeed();

    if (millis() - startTime > HOMING_TIMEOUT_MS) {
      Serial.println("ERR homing timeout on Z");
      return false;
    }
  }

  motorZ.setSpeed(0);
  motorA.setSpeed(0);
  motorZ.setCurrentPosition(0);
  motorA.setCurrentPosition(0);

  Serial.println("OK Z homed");
  return true;
}

bool homeCommand(String axis) {
  axis.trim();
  axis.toUpperCase();

  bool ok = true;

  if (axis == "" || axis == "X") {
    ok = homeSingleAxis(motorX, X_LIMIT_PIN, X_HOMING_SPEED, "X") && ok;
    delay(300);
  }

  if (axis == "" || axis == "Y") {
    ok = homeSingleAxis(motorY, Y_LIMIT_PIN, Y_HOMING_SPEED, "Y") && ok;
    delay(300);
  }

  if (axis == "" || axis == "Z") {
    ok = homeZAxisWithA() && ok;
    delay(300);
  }

  if (axis != "" && axis != "X" && axis != "Y" && axis != "Z") {
    Serial.println("ERR unknown home axis");
    return false;
  }

  if (ok) {
    Serial.println("OK homing complete");
    printPosition();
  }

  return ok;
}

String nextToken(String &s) {
  s.trim();
  if (s.length() == 0) return "";

  int idx = s.indexOf(' ');
  if (idx == -1) {
    String token = s;
    s = "";
    token.trim();
    return token;
  }

  String token = s.substring(0, idx);
  s = s.substring(idx + 1);
  token.trim();
  return token;
}

void handleMoveCommand(String args) {
  float targetX = currentXmm();
  float targetY = currentYmm();
  float targetZ = currentZmm();
  float targetR = currentRdeg();

  while (args.length() > 0) {
    String token = nextToken(args);
    if (token.length() == 0) continue;

    token.replace("=", "");
    token.toUpperCase();

    char axis = token.charAt(0);
    String valueString = token.substring(1);

    // Also support: MOVE X 10 instead of MOVE X10
    if (valueString.length() == 0 && args.length() > 0) {
      valueString = nextToken(args);
    }

    float value = valueString.toFloat();

    if (axis == 'X') targetX = value;
    else if (axis == 'Y') targetY = value;
    else if (axis == 'Z') targetZ = value;
    else if (axis == 'R') targetR = value;
    else {
      Serial.print("ERR unknown MOVE axis: ");
      Serial.println(axis);
      return;
    }
  }

  moveToMm(targetX, targetY, targetZ, targetR);
  Serial.println("OK moving");
}

void handleOldStepCommand(String axis, long steps) {
  axis.toUpperCase();

  Serial.print("Command: ");
  Serial.print(axis);
  Serial.print(" ");
  Serial.println(steps);

  if (axis == "X") {
    motorX.move(steps);
  }
  else if (axis == "Y") {
    motorY.move(steps);
  }
  else if (axis == "Z") {
    motorZ.move(steps);
    motorA.move(steps); // keep both Z motors synchronized
  }
  else if (axis == "A") {
    motorA.move(steps); // manual A-only command kept for debugging
  }
  else if (axis == "R") {
    motorR.move(steps);
  }
  else if (axis == "ALL") {
    motorX.move(steps);
    motorY.move(steps);
    motorZ.move(steps);
    motorA.move(steps);
    motorR.move(steps);
  }
  else {
    Serial.println("ERR unknown motor command");
  }
}

void handleCommand(String command) {
  command.trim();
  if (command.length() == 0) return;

  String lowerCommand = command;
  lowerCommand.toLowerCase();

  if (lowerCommand == "on") {
    vacuumOn();
    return;
  }

  if (lowerCommand == "off") {
    vacuumOff();
    return;
  }

  String upperCommand = command;
  upperCommand.toUpperCase();

  if (upperCommand == "STATUS") {
    printPosition();
    Serial.println("OK");
    return;
  }

  if (upperCommand == "ZERO") {
    zeroAllAxes();
    return;
  }

  if (upperCommand == "STOP") {
    stopAllMotorsNow();
    return;
  }

  if (upperCommand.startsWith("SPEED")) {
    String speedString = command.substring(5);
    speedString.trim();
    float speed = speedString.toFloat();
    if (speed <= 0) {
      Serial.println("ERR invalid speed");
      return;
    }
    setupSpeeds(speed);
    Serial.print("OK speed=");
    Serial.println(speed);
    return;
  }

  if (upperCommand == "VAC ON") {
    vacuumOn();
    return;
  }

  if (upperCommand == "VAC OFF") {
    vacuumOff();
    return;
  }

  if (upperCommand == "PICK") {
    moveZToMm(Z_PICK);
    runUntilDone();
    vacuumOn();
    delay(300);
    moveZToMm(Z_TRAVEL);
    runUntilDone();
    Serial.println("OK pick done");
    return;
  }

  if (upperCommand == "PLACE") {
    moveZToMm(Z_PLACE);
    runUntilDone();
    vacuumOff();
    delay(300);
    moveZToMm(Z_TRAVEL);
    runUntilDone();
    Serial.println("OK place done");
    return;
  }

  if (upperCommand == "HOME") {
    homeCommand("");
    return;
  }

  if (upperCommand.startsWith("HOME ")) {
    String axis = command.substring(5);
    homeCommand(axis);
    return;
  }

  if (upperCommand.startsWith("MOVE")) {
    String args = command.substring(4);
    handleMoveCommand(args);
    return;
  }

  // Old style commands: "X 200", "Y -100", "ALL 100", etc.
  int spaceIndex = command.indexOf(' ');
  if (spaceIndex != -1) {
    String axis = command.substring(0, spaceIndex);
    String stepsString = command.substring(spaceIndex + 1);
    axis.trim();
    stepsString.trim();

    long steps = stepsString.toInt();
    handleOldStepCommand(axis, steps);
    return;
  }

  Serial.println("ERR invalid command");
}

void setup() {
  Serial.begin(9600);

  pinMode(ENABLE_PIN, OUTPUT);
  digitalWrite(ENABLE_PIN, LOW); // LOW = enable CNC shield motors

  pinMode(ROT_ENABLE_PIN, OUTPUT);
  digitalWrite(ROT_ENABLE_PIN, LOW); // LOW = enable rotation motor driver

  pinMode(PUMP_PIN, OUTPUT);
  digitalWrite(PUMP_PIN, LOW); // Pump OFF at start

  pinMode(X_LIMIT_PIN, INPUT_PULLUP);
  pinMode(Y_LIMIT_PIN, INPUT_PULLUP);
  pinMode(Z_LIMIT_PIN, INPUT_PULLUP);

  // Keep your mirrored A motor behavior.
  motorA.setPinsInverted(true, false, true);

  setupSpeeds(20.0);

  printHelp();
  printPosition();
}

void loop() {
  if (Serial.available()) {
    String command = Serial.readStringUntil('\n');
    handleCommand(command);
  }

  runAllMotors();
  printPositionIfChanged();
}
