// Placement engine — uses the clean MOVE/PICK/PLACE protocol
//
// Per component:
//   1. MOVE to feeder X,Y at travel Z, R=0
//   2. PICK (Arduino lowers Z to zPick, pump on, raises Z)
//   3. MOVE to PCB target X,Y at travel Z with rotation R
//   4. PLACE (Arduino lowers Z to zPlace, pump off, raises Z)
//
// All coordinates are in STEPS. The .pos file is in mm, converted using
// stepsPerMm from the config.

class PlacementEngine {
  constructor({ sendCommand, log, onUpdate }) {
    this.sendCommand = sendCommand;
    this.log = log;
    this.onUpdate = onUpdate;

    this.state = "idle";
    this.queue = [];
    this.index = 0;
    this.config = null;
    this._abort = false;
  }

  load(components, config) {
    this.config = config;
    this.queue = [];

    const stepsPerMm = config.stepsPerMm || 1;

    for (const c of components) {
      const feeder = config.feeders[c.value];
      if (!feeder) {
        this.log(`⚠️  No feeder mapped for ${c.ref} (${c.value}) — will be skipped`);
        this.queue.push({ component: c, feeder: null, skipped: true });
        continue;
      }

      // .pos coords are in mm. Convert to steps, then apply PCB origin offset + rotation.
      const cos = Math.cos((config.pcb.rotation || 0) * Math.PI / 180);
      const sin = Math.sin((config.pcb.rotation || 0) * Math.PI / 180);

      const compX = c.x * stepsPerMm;
      const compY = c.y * stepsPerMm;

      const targetX = config.pcb.x + (compX * cos - compY * sin);
      const targetY = config.pcb.y + (compX * sin + compY * cos);
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

  // Manual mode: components are already in machine steps, no mm conversion or PCB offset.
  // Each component must have: { ref, value (feeder key), x, y, z, rotation } in machine steps.
  loadManual(manualComponents, config) {
    this.config = config;
    this.queue = [];

    for (const c of manualComponents) {
      const feeder = config.feeders[c.value];
      if (!feeder) {
        this.log(`⚠️  No feeder mapped for ${c.ref} (${c.value}) — will be skipped`);
        this.queue.push({ component: c, feeder: null, skipped: true, manualZ: c.z });
        continue;
      }

      this.queue.push({
        component: c,
        feeder,
        target: { x: c.x, y: c.y, r: c.rotation || 0 },
        manualZ: c.z,    // per-component place Z (overrides config.pcb.z for this entry)
        skipped: false,
      });
    }

    this.index = 0;
    this.state = "idle";
    this.manualMode = true;
    this._emit();
    this.log(`🧪 Loaded ${this.queue.length} test components`);
  }

  async start() {
    if (this.state === "running") return;
    if (!this.queue.length) { this.log("Nothing to place"); return; }

    this.state = "running";
    this._abort = false;
    this._emit();
    this.log("▶️  Placement started");

    while (this.index < this.queue.length && !this._abort) {
      if (this.state === "paused") { await this._sleep(200); continue; }
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

    if (this._abort) { this.log("⏹️  Placement aborted"); this.state = "idle"; }
    else             { this.log("✅ Placement complete"); this.state = "done"; }
    this._emit();
  }

  pause()  { if (this.state === "running") { this.state = "paused"; this._emit(); this.log("⏸️  Paused"); } }
  resume() { if (this.state === "paused")  { this.state = "running"; this._emit(); this.log("▶️  Resumed"); } }
  stop()   { this._abort = true; }
  skipCurrent() { this.index++; this._emit(); this.log("⏭️  Skipped current component"); }

  async _placeOne(item) {
    const { component, feeder, target } = item;
    const cfg = this.config;
    const zTravel = cfg.zTravel || 0;
    const feederZ = feeder.z ?? 0;
    // Use per-component Z if provided (manual/test mode), else fall back to global PCB Z
    const placeZ = (item.manualZ !== undefined && item.manualZ !== null) ? item.manualZ : (cfg.pcb.z ?? 0);

    this.log(`📦 [${this.index+1}/${this.queue.length}] ${component.ref} (${component.value})`);

    // ─── 1. TRAVEL TO FEEDER ──────────────────────────────────────
    // Safe travel: lift Z first → move XY → lower Z
    await this.sendCommand(`MOVE Z${Math.round(zTravel)}`);
    await this.sendCommand(`MOVE X${Math.round(feeder.x)} Y${Math.round(feeder.y)} R0`);

    // ─── 2. PICK ──────────────────────────────────────────────────
    await this.sendCommand(`MOVE Z${Math.round(feederZ)}`);     // plunge into pocket
    await this.sendCommand("VAC ON");
    await this._sleep(200);
    await this.sendCommand(`MOVE Z${Math.round(zTravel)}`);     // lift out

    // ─── 3. TRAVEL TO PCB ─────────────────────────────────────────
    // Already at travel Z, so XY+R only
    await this.sendCommand(`MOVE X${Math.round(target.x)} Y${Math.round(target.y)} R${target.r.toFixed(1)}`);

    // ─── 4. PLACE ─────────────────────────────────────────────────
    await this.sendCommand(`MOVE Z${Math.round(placeZ)}`);        // plunge onto board
    await this.sendCommand("VAC OFF");
    await this._sleep(200);
    await this.sendCommand(`MOVE Z${Math.round(zTravel)}`);     // lift off

    // ─── 5. ADVANCE TAPE (round trip back to feeder) ──────────────
    // Only if this feeder has a pitch configured.
    if (feeder.pitch && feeder.pitch > 0) {
      this.log(`   ↳ advancing tape for next pick (${feeder.pitch} steps)`);
      // 5a. Travel back to feeder XY at travel Z
      await this.sendCommand(`MOVE X${Math.round(feeder.x)} Y${Math.round(feeder.y)} R0`);
      // 5b. Plunge into the (just-emptied) pocket
      await this.sendCommand(`MOVE Z${Math.round(feederZ)}`);
      // 5c. Run ADVANCE: drag tape, lift, return Y, plunge back to pocket
      await this.sendCommand(`ADVANCE Y${Math.round(feeder.pitch)} Z${Math.round(zTravel)}`);
      // 5d. Lift out of pocket — ready for next cycle's travel move
      await this.sendCommand(`MOVE Z${Math.round(zTravel)}`);
    }
  }

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
