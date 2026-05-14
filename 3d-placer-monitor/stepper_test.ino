/*
 * 3D Placer Web Monitor — Stepper Test Sketch
 * Hardware: Arduino Mega + A4988/DRV8825 + NEMA 17
 *
 * Dashboard commands:
 *   FWD          → spin forward 1 full revolution (200 steps)
 *   BWD          → spin backward 1 full revolution
 *   STEP <n>     → move n steps (positive = forward, negative = backward)
 *   SPEED <us>   → set step delay in microseconds (lower = faster, min 200)
 *   STOP         → stop any ongoing movement
 *   HOME         → reset position counter to 0
 *   STATUS       → print current position
 */

// ─── PIN CONFIG ──────────────────────────────────────────────────────────
const int STEP_PIN   = 3;
const int DIR_PIN    = 4;
const int ENABLE_PIN = 5;  // LOW = enabled, HIGH = disabled (motor free)

// ─── MOTOR STATE ─────────────────────────────────────────────────────────
long   currentPosition = 0;        // in steps
long   targetPosition  = 0;        // in steps
int    stepDelay       = 800;      // microseconds between steps (slower = safer to start)
bool   moving          = false;

// ─── SERIAL BUFFER ───────────────────────────────────────────────────────
String inputBuffer = "";

// ─── REPORTING ───────────────────────────────────────────────────────────
unsigned long lastReport = 0;
const unsigned long REPORT_INTERVAL = 100;  // ms

// ─────────────────────────────────────────────────────────────────────────
void setup() {
  Serial.begin(9600);

  pinMode(STEP_PIN, OUTPUT);
  pinMode(DIR_PIN, OUTPUT);
  pinMode(ENABLE_PIN, OUTPUT);

  digitalWrite(ENABLE_PIN, LOW);   // enable the driver
  digitalWrite(STEP_PIN, LOW);
  digitalWrite(DIR_PIN, LOW);

  Serial.println("Stepper test ready.");
  Serial.println("Commands: FWD, BWD, STEP <n>, SPEED <us>, STOP, HOME, STATUS");
}

// ─────────────────────────────────────────────────────────────────────────
void loop() {
  // 1. Read incoming commands from dashboard
  readSerial();

  // 2. Step the motor if moving
  doStep();

  // 3. Report position back to dashboard (~10x per second)
  if (millis() - lastReport > REPORT_INTERVAL) {
    reportPosition();
    lastReport = millis();
  }
}

// ─── READ COMMANDS FROM SERIAL ───────────────────────────────────────────
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

// ─── HANDLE A COMPLETE COMMAND ───────────────────────────────────────────
void handleCommand(String cmd) {
  cmd.trim();
  cmd.toUpperCase();

  Serial.print("CMD: "); Serial.println(cmd);

  if (cmd == "FWD") {
    targetPosition = currentPosition + 200;   // 1 full rev forward
    moving = true;
  }
  else if (cmd == "BWD") {
    targetPosition = currentPosition - 200;   // 1 full rev backward
    moving = true;
  }
  else if (cmd.startsWith("STEP ")) {
    long n = cmd.substring(5).toInt();
    targetPosition = currentPosition + n;
    moving = true;
    Serial.print("Moving "); Serial.print(n); Serial.println(" steps");
  }
  else if (cmd.startsWith("SPEED ")) {
    int s = cmd.substring(6).toInt();
    if (s >= 200 && s <= 5000) {
      stepDelay = s;
      Serial.print("Speed set to "); Serial.print(stepDelay); Serial.println(" us");
    } else {
      Serial.println("Speed out of range (200-5000 us)");
    }
  }
  else if (cmd == "STOP") {
    targetPosition = currentPosition;
    moving = false;
    Serial.println("Stopped");
  }
  else if (cmd == "HOME") {
    currentPosition = 0;
    targetPosition  = 0;
    moving = false;
    Serial.println("Position reset to 0");
  }
  else if (cmd == "STATUS") {
    Serial.print("Position: "); Serial.println(currentPosition);
    Serial.print("Target:   "); Serial.println(targetPosition);
    Serial.print("Speed:    "); Serial.print(stepDelay); Serial.println(" us");
  }
  else {
    Serial.print("Unknown command: "); Serial.println(cmd);
  }
}

// ─── PERFORM ONE STEP IF NEEDED ──────────────────────────────────────────
void doStep() {
  if (!moving) return;
  if (currentPosition == targetPosition) {
    moving = false;
    return;
  }

  // Set direction
  if (targetPosition > currentPosition) {
    digitalWrite(DIR_PIN, HIGH);
  } else {
    digitalWrite(DIR_PIN, LOW);
  }

  // One step pulse
  digitalWrite(STEP_PIN, HIGH);
  delayMicroseconds(5);                  // pulse width
  digitalWrite(STEP_PIN, LOW);
  delayMicroseconds(stepDelay);          // pause between steps

  // Update position
  if (targetPosition > currentPosition) currentPosition++;
  else                                  currentPosition--;
}

// ─── REPORT POSITION TO DASHBOARD ────────────────────────────────────────
void reportPosition() {
  // Format the dashboard expects: X:value Y:value Z:value
  // We'll map: X = stepper position, Y = target, Z = speed
  Serial.print("X:");
  Serial.print(currentPosition);
  Serial.print(" Y:");
  Serial.print(targetPosition);
  Serial.print(" Z:");
  Serial.println(stepDelay);
}
