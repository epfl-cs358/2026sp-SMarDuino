const express = require("express");
const { SerialPort } = require("serialport");
const { ReadlineParser } = require("@serialport/parser-readline");
const { WebSocketServer } = require("ws");
const path = require("path");
const fs = require("fs");
const { parseKiCadPos } = require("./parser");
const { PlacementEngine } = require("./placement");

// ─── CONFIG ──────────────────────────────────────────────────────────────────
const HTTP_PORT = 3000;
const BAUD_RATE = parseInt(process.env.BAUD_RATE) || 9600;
const CONFIG_FILE = path.join(__dirname, "config.json");

const DEFAULT_CONFIG = {
  pcb:    { x: 100, y: 100, rotation: 0 },
  camera: { x: 50, y: 200 },
  feeders: {},
  zTravel: 10,
};

let machineConfig = loadConfig();

function loadConfig() {
  try {
    if (fs.existsSync(CONFIG_FILE)) {
      return { ...DEFAULT_CONFIG, ...JSON.parse(fs.readFileSync(CONFIG_FILE, "utf8")) };
    }
  } catch (e) { console.error("Config load failed:", e.message); }
  return { ...DEFAULT_CONFIG };
}
function saveConfig() {
  fs.writeFileSync(CONFIG_FILE, JSON.stringify(machineConfig, null, 2));
}

const app = express();
app.use(express.json({ limit: "10mb" }));
app.use(express.text({ limit: "10mb", type: "text/*" }));
app.use(express.static(path.join(__dirname, "public")));

const server = app.listen(HTTP_PORT, () => {
  console.log(`\n🌐 Dashboard running at http://localhost:${HTTP_PORT}`);
});

const wss = new WebSocketServer({ server });
function broadcast(data) {
  const msg = JSON.stringify(data);
  wss.clients.forEach((c) => { if (c.readyState === 1) c.send(msg); });
}

// ─── SERIAL ─────────────────────────────────────────────────────────────────
let port = null;
let serialConnected = false;
let currentPortPath = process.env.SERIAL_PORT || null;
let retryTimer = null;
let pendingResolve = null;

function connectSerial(targetPort) {
  if (targetPort) currentPortPath = targetPort;
  if (!currentPortPath) {
    broadcast({ type: "serial_status", connected: false, port: null });
    return;
  }

  console.log(`📡 Connecting to ${currentPortPath} @ ${BAUD_RATE} baud...`);

  try {
    port = new SerialPort({ path: currentPortPath, baudRate: BAUD_RATE, autoOpen: false });

    port.open((err) => {
      if (err) {
        console.error(`❌ ${err.message}`);
        serialConnected = false;
        broadcast({ type: "serial_status", connected: false, port: currentPortPath, error: err.message });
        retryTimer = setTimeout(() => connectSerial(), 5000);
        return;
      }
      console.log(`✅ Connected to ${currentPortPath}`);
      serialConnected = true;
      broadcast({ type: "serial_status", connected: true, port: currentPortPath });
    });

    const parser = port.pipe(new ReadlineParser({ delimiter: "\n" }));

    parser.on("data", (line) => {
      line = line.trim();
      if (!line) return;
      console.log("Serial:", line);
      broadcast({ type: "log", message: line, timestamp: new Date().toISOString() });

      const xM = line.match(/X[:\s]([-\d.]+)/i);
      const yM = line.match(/Y[:\s]([-\d.]+)/i);
      const zM = line.match(/Z[:\s]([-\d.]+)/i);
      const rM = line.match(/R[:\s]([-\d.]+)/i);
      if (xM || yM || zM || rM) {
        const pos = {};
        if (xM) pos.x = parseFloat(xM[1]);
        if (yM) pos.y = parseFloat(yM[1]);
        if (zM) pos.z = parseFloat(zM[1]);
        if (rM) pos.r = parseFloat(rM[1]);
        broadcast({ type: "position", ...pos });
      }

      if (pendingResolve) {
        if (/^OK\b/.test(line)) { pendingResolve.resolve(line); pendingResolve = null; }
        else if (/^ERR/i.test(line)) { pendingResolve.reject(new Error(line)); pendingResolve = null; }
      }
    });

    port.on("error", (err) => {
      console.error("Serial error:", err.message);
      serialConnected = false;
      broadcast({ type: "serial_status", connected: false, port: currentPortPath });
    });

    port.on("close", () => {
      if (serialConnected) {
        serialConnected = false;
        broadcast({ type: "serial_status", connected: false, port: currentPortPath });
        retryTimer = setTimeout(() => connectSerial(), 5000);
      }
    });

  } catch (e) {
    console.error("Serial setup error:", e.message);
    retryTimer = setTimeout(() => connectSerial(), 5000);
  }
}

function sendAndWait(cmd, timeoutMs = 30000) {
  return new Promise((resolve, reject) => {
    if (!port || !serialConnected) return reject(new Error("Serial not connected"));
    if (pendingResolve) return reject(new Error("Another command is pending"));

    const timer = setTimeout(() => {
      pendingResolve = null;
      reject(new Error("Command timed out"));
    }, timeoutMs);

    pendingResolve = {
      resolve: (line) => { clearTimeout(timer); resolve(line); },
      reject:  (err)  => { clearTimeout(timer); reject(err); },
    };

    port.write(cmd + "\n");
    console.log("→ Arduino:", cmd);
  });
}

// ─── PLACEMENT ENGINE ───────────────────────────────────────────────────────
const engine = new PlacementEngine({
  sendCommand: sendAndWait,
  log: (msg) => broadcast({ type: "log", message: msg, timestamp: new Date().toISOString() }),
  onUpdate: (state) => broadcast({ type: "placement_state", ...state }),
});

let lastComponents = [];

// ─── WEBSOCKETS ─────────────────────────────────────────────────────────────
wss.on("connection", (ws) => {
  ws.send(JSON.stringify({ type: "serial_status", connected: serialConnected, port: currentPortPath }));
  ws.send(JSON.stringify({ type: "config", config: machineConfig }));
  ws.send(JSON.stringify({ type: "components", components: lastComponents }));

  ws.on("message", (msg) => {
    try {
      const data = JSON.parse(msg);
      if (data.type === "command" && port && serialConnected) {
        port.write(data.command.trim() + "\n");
        console.log("→ Arduino (manual):", data.command);
      }
    } catch (e) {}
  });
});

// ─── REST API ───────────────────────────────────────────────────────────────
app.get("/api/ports", async (req, res) => {
  try { const ports = await SerialPort.list(); res.json({ ports, current: currentPortPath }); }
  catch (e) { res.status(500).json({ error: e.message }); }
});

app.post("/api/connect", (req, res) => {
  const { port: newPort } = req.body;
  if (!newPort) return res.status(400).json({ error: "No port specified" });
  if (retryTimer) { clearTimeout(retryTimer); retryTimer = null; }
  if (port && port.isOpen) port.close(() => { port = null; serialConnected = false; connectSerial(newPort); });
  else connectSerial(newPort);
  res.json({ ok: true, port: newPort });
});

app.get("/api/config", (req, res) => res.json(machineConfig));
app.post("/api/config", (req, res) => {
  machineConfig = { ...machineConfig, ...req.body };
  saveConfig();
  broadcast({ type: "config", config: machineConfig });
  res.json({ ok: true });
});

app.post("/api/upload-pos", (req, res) => {
  try {
    const text = typeof req.body === "string" ? req.body : req.body.text;
    if (!text) return res.status(400).json({ error: "No file content" });
    lastComponents = parseKiCadPos(text);
    broadcast({ type: "components", components: lastComponents });
    broadcast({ type: "log", message: `📂 Parsed ${lastComponents.length} components from .pos file`, timestamp: new Date().toISOString() });
    res.json({ ok: true, count: lastComponents.length, components: lastComponents });
  } catch (e) { res.status(500).json({ error: e.message }); }
});

app.post("/api/placement/start", async (req, res) => {
  if (!lastComponents.length) return res.status(400).json({ error: "No components loaded" });
  engine.load(lastComponents, machineConfig);
  engine.start();
  res.json({ ok: true });
});
app.post("/api/placement/pause",  (req, res) => { engine.pause();        res.json({ ok: true }); });
app.post("/api/placement/resume", (req, res) => { engine.resume();       res.json({ ok: true }); });
app.post("/api/placement/stop",   (req, res) => { engine.stop();         res.json({ ok: true }); });
app.post("/api/placement/skip",   (req, res) => { engine.skipCurrent();  res.json({ ok: true }); });

app.post("/api/jog", async (req, res) => {
  const { x, y, z, r } = req.body;
  const parts = [];
  if (x !== undefined) parts.push(`X${x}`);
  if (y !== undefined) parts.push(`Y${y}`);
  if (z !== undefined) parts.push(`Z${z}`);
  if (r !== undefined) parts.push(`R${r}`);
  try { await sendAndWait(`MOVE ${parts.join(" ")}`); res.json({ ok: true }); }
  catch (e) { res.status(500).json({ error: e.message }); }
});

// ─── AUTO-DETECT ────────────────────────────────────────────────────────────
(async () => {
  if (currentPortPath) return connectSerial();
  try {
    const ports = await SerialPort.list();
    const arduino = ports.find(p =>
      /arduino|ch340|ch341|ftdi|usb.*serial|serial.*usb/i.test(
        (p.manufacturer || "") + (p.pnpId || "") + (p.vendorId || "")
      ));
    if (arduino) { console.log(`🔍 Auto-detected on ${arduino.path}`); connectSerial(arduino.path); }
    else { broadcast({ type: "serial_status", connected: false, port: null }); }
  } catch (e) { console.error("Port detection error:", e.message); }
})();
