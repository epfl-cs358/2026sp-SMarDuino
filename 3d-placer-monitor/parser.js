// KiCad .pos file parser
// Handles the standard CSV-ish format KiCad exports

function parseKiCadPos(text) {
  const lines = text.split(/\r?\n/);
  const components = [];

  for (const rawLine of lines) {
    const line = rawLine.trim();

    // Skip blank lines and comments/headers
    if (!line || line.startsWith("#") || line.startsWith("//")) continue;

    // KiCad format (whitespace-separated):
    // Ref  Val  Package  PosX  PosY  Rot  Side
    // C1   100nF  C_0402  10.50  25.30  0  top
    //
    // Also supports CSV: "Ref","Val","Package","PosX","PosY","Rot","Side"
    let parts;
    if (line.includes(",")) {
      // CSV — strip quotes
      parts = line.split(",").map(s => s.trim().replace(/^"|"$/g, ""));
    } else {
      parts = line.split(/\s+/);
    }

    if (parts.length < 6) continue;

    const ref     = parts[0];
    const val     = parts[1];
    const pkg     = parts[2];
    const posX    = parseFloat(parts[3]);
    const posY    = parseFloat(parts[4]);
    const rot     = parseFloat(parts[5]);
    const side    = (parts[6] || "top").toLowerCase();

    // Skip if numeric parsing failed (probably a header line)
    if (isNaN(posX) || isNaN(posY) || isNaN(rot)) continue;

    components.push({
      ref,            // e.g. "R1"
      value: val,     // e.g. "10k"
      package: pkg,   // e.g. "R_0603"
      x: posX,        // mm, relative to PCB origin
      y: posY,
      rotation: rot,  // degrees
      side,           // "top" or "bottom"
    });
  }

  return components;
}

module.exports = { parseKiCadPos };
