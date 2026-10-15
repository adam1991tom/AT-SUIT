// What this laptop reports to the server, like a task manager: CPU, memory,
// disk, network and screens. And, only when the venue turns snapshots on and a
// manager is looking, small pictures of each screen. Pictures are made in
// memory and sent straight to the venue's own server; nothing is saved here.
const fs = require("fs");
const os = require("os");
const { app, desktopCapturer, net, powerMonitor, screen } = require("electron");

let lastCpu = null;

function cpuTimes() {
  let idle = 0, total = 0;
  for (const c of os.cpus()) {
    for (const v of Object.values(c.times)) total += v;
    idle += c.times.idle;
  }
  return { idle, total };
}

// Percent busy since the last call (the heartbeat, every 10 seconds).
function cpuPercent() {
  const now = cpuTimes();
  const prev = lastCpu;
  lastCpu = now;
  if (!prev || now.total <= prev.total) return null;
  return Math.round(100 * (1 - (now.idle - prev.idle) / (now.total - prev.total)));
}

async function disk() {
  const root = process.platform === "win32" ? `${process.env.SystemDrive || "C:"}\\` : "/";
  try {
    const s = await fs.promises.statfs(root);
    return { drive: root, total: s.blocks * s.bsize, free: s.bavail * s.bsize };
  } catch (_) {
    return null;
  }
}

function network() {
  const out = [];
  for (const [name, addrs] of Object.entries(os.networkInterfaces())) {
    for (const a of addrs || []) {
      if (!a.internal && a.family === "IPv4") out.push({ name, ip: a.address });
    }
  }
  return out.slice(0, 6);
}

async function system(extra = {}) {
  const cpus = os.cpus();
  const appMem = app.getAppMetrics().reduce((n, m) => n + (m.memory?.workingSetSize || 0), 0) * 1024;
  let battery = null;
  try { battery = powerMonitor.isOnBatteryPower() ? "battery" : "mains"; } catch (_) {}
  return {
    at: Date.now(),
    cpu: cpuPercent(),
    cpu_model: (cpus[0]?.model || "").trim().slice(0, 80),
    cores: cpus.length,
    mem_total: os.totalmem(),
    mem_free: os.freemem(),
    app_mem: appMem,
    disk: await disk(),
    net: network(),
    online: net.isOnline(),
    power: battery,
    displays: screen.getAllDisplays().map((d) => ({ w: d.size.width, h: d.size.height, primary: d.id === screen.getPrimaryDisplay().id })),
    os: `${os.type()} ${os.release()}`,
    host: os.hostname(),
    uptime: Math.round(os.uptime()),
    app_uptime: Math.round(process.uptime()),
    app_version: app.getVersion(),
    ...extra,
  };
}

// One small JPEG per screen. About 20 to 40 KB each.
async function snapshot() {
  const sources = await desktopCapturer.getSources({ types: ["screen"], thumbnailSize: { width: 480, height: 270 } });
  return sources.slice(0, 4).map((s, i) => ({
    name: sources.length > 1 ? `Screen ${i + 1}` : "Screen",
    image: `data:image/jpeg;base64,${s.thumbnail.toJPEG(60).toString("base64")}`,
  }));
}

module.exports = { system, snapshot };
