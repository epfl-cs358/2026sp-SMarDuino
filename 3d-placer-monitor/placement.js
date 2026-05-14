// Placement engine — drives the machine through the pick & place sequence
//
// State machine flow per component:
//   1. MOVE to feeder (X,Y at travel Z)
//   2. PICK (lower Z, vacuum on, raise Z)
//   3. MOVE to PCB target with rotation
//   4. PLACE (lower Z, vacuum off, raise Z)
//   5. → next component

class PlacementEngine {
  constructor({ sendCommand, log, onUpdate }) {
    this.sendCommand = sendCommand;   // (cmd) => sends to Arduino, returns promise<OK>
    this.log = log;
    this.onUpdate = onUpdate;         // called whenever state changes

    this.state = "idle";              // idle | running | paused | done | error
    this.queue = [];                  // [{ component, feeder, target }]
    this.index = 0;
    this.config = null;
    this._abort = false;
  }

  // ─── Configuration ─────────────────────────────────────────────────────
  // {
  //   pcb:      { x: 100, y: 100, rotation: 0 },
  //   camera:   { x: 50, y: 200 },
  //   feeders:  { "10k": { x: 10, y: 200 }, "100nF": { x: 30, y: 200 }, ... },
  //   zTravel:  10
  // }
  load(components, config) {
    this.config = config;
    this.queue = [];

    for (const c of components) {
      const feeder = config.feeders[c.value];
      if (!feeder) {
        this.log(`⚠️  No feeder mapped for ${c.ref} (${c.value}) — will be skipped`);
        this.queue.push({ component: c, feeder: null, skipped: true });
        continue;
      }

      // Apply PCB origin offset + rotation
      const cos = Math.cos((config.pcb.rotation || 0) * Math.PI / 180);
      const sin = Math.sin((config.pcb.rotation || 0) * Math.PI / 180);

      const targetX = config.pcb.x + (c.x * cos - c.y * sin);
      const targetY = config.pcb.y + (c.x * sin + c.y * cos);
      const targetR = c.rotation + (config.pcb.rotation || 0);

      this.queue.push({
        component: c,
        feeder,
        target: { x: targetX, y: targetY, r: targetR },
        skipped: false,
      });
    }

    this.index = 0;
    this.state = "idle";
    this._emit();
    this.log(`📋 Loaded ${this.queue.length} components`);
  }

  // ─── Control ───────────────────────────────────────────────────────────
  async start() {
    if (this.state === "running") return;
    if (!this.queue.length) { this.log("Nothing to place"); return; }

    this.state = "running";
    this._abort = false;
    this._emit();
    this.log("▶️  Placement started");

    while (this.index < this.queue.length && !this._abort) {
      if (this.state === "paused") {
        await this._sleep(200);
        continue;
      }

      const item = this.queue[this.index];

      if (item.skipped) {
        this.log(`⏭️  Skipping ${item.component.ref} (no feeder)`);
        this.index++;
        this._emit();
        continue;
      }

      try {
        await this._placeOne(item);
        this.index++;
        this._emit();
      } catch (err) {
        this.log(`❌ Error on ${item.component.ref}: ${err.message}`);
        this.state = "error";
        this._emit();
        return;
      }
    }

    if (this._abort) {
      this.log("⏹️  Placement aborted");
      this.state = "idle";
    } else {
      this.log("✅ Placement complete");
      this.state = "done";
    }
    this._emit();
  }

  pause()  { if (this.state === "running") { this.state = "paused"; this._emit(); this.log("⏸️  Paused"); } }
  resume() { if (this.state === "paused")  { this.state = "running"; this._emit(); this.log("▶️  Resumed"); } }
  stop()   { this._abort = true; }
  skipCurrent() { this.index++; this._emit(); this.log("⏭️  Skipped current component"); }
  reset()  { this.index = 0; this.state = "idle"; this._abort = false; this._emit(); }

  // ─── Place one component ──────────────────────────────────────────────
  async _placeOne(item) {
    const { component, feeder, target } = item;
    const z = this.config.zTravel || 10;

    this.log(`📦 [${this.index+1}/${this.queue.length}] ${component.ref} (${component.value})`);

    // 1. Travel to feeder
    await this.sendCommand(`MOVE X${feeder.x} Y${feeder.y} Z${z} R0`);
    // 2. Pick
    await this.sendCommand("PICK");
    // 3. Travel to PCB target with rotation
    await this.sendCommand(`MOVE X${target.x.toFixed(2)} Y${target.y.toFixed(2)} Z${z} R${target.r.toFixed(1)}`);
    // 4. Place
    await this.sendCommand("PLACE");
  }

  // ─── Helpers ───────────────────────────────────────────────────────────
  _sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

  _emit() {
    this.onUpdate && this.onUpdate({
      state: this.state,
      index: this.index,
      total: this.queue.length,
      queue: this.queue.map((q, i) => ({
        ref: q.component.ref,
        value: q.component.value,
        x: q.component.x,
        y: q.component.y,
        rotation: q.component.rotation,
        skipped: q.skipped,
        done: i < this.index,
        current: i === this.index && this.state === "running",
      })),
    });
  }
}

module.exports = { PlacementEngine };
