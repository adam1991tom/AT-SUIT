// The laptop bubble: captions made on this laptop stay here.
//
// When a tech's mic is captioning on this laptop, the text is tidied here the same way the
// server tidies it, shown in the workspace, and fed to the room's caption and timer screens
// straight from this laptop (a small screen feed on the venue network, see startScreens).
// If the venue keeps caption text on the laptops, nothing of it goes to the server: the
// transcript is written here and deleted after the venue's hours, and the tech saves it from
// the workspace.
const fs = require("fs");
const http = require("http");
const os = require("os");
const path = require("path");
const { app } = require("electron");
const { WebSocketServer } = require("ws");

const { tidy, caseWords, isFillerSpam, MUSIC_LABEL } = require("./caption-text");

// ------------------------------------------------------------ state --
let cfg = { room_id: null, private: false, options: {}, keep: {}, keep_hours: 12 };
const recent = { finals: [], finals_at: [], partial: "" };
let live = false;

// The workspace says which room this laptop captions and how: { room_id, private, options:
// { join_acronyms, sentence_case, music_label }, vocabulary: [lines], keep_hours }.
function configure(c) {
  const room = Number(c.room_id) || null;
  if (room !== cfg.room_id) { recent.finals = []; recent.finals_at = []; recent.partial = ""; }
  cfg = {
    room_id: room,
    private: !!c.private,
    options: c.options || {},
    keep: caseWords(c.vocabulary || []),
    keep_hours: Math.max(1, Math.min(168, Number(c.keep_hours) || 12)),
  };
  purge();
}

function setLive(on) {
  live = !!on;
  if (cfg.room_id) publish(`captions:${cfg.room_id}`, "status", { live, source: os.hostname(), local: true });
  if (!live) recent.partial = "";
}

// What the captions engine heard (raw, from captions-worker.js). Returns the tidied event the
// workspace shows, or null when there's nothing to show.
function heard(m) {
  if (!cfg.room_id || !live || (m.type !== "partial" && m.type !== "final")) return null;
  let text = tidy(m.text, cfg.options, cfg.keep);
  if (m.type === "final") {
    if (!text) return null;
    if (cfg.options.music_label !== false && isFillerSpam(text)) text = MUSIC_LABEL;
    recent.finals.push(text); recent.finals_at.push(Date.now() / 1000);
    if (recent.finals.length > 8) { recent.finals.shift(); recent.finals_at.shift(); }
    recent.partial = "";
    if (cfg.private) write(text);
  } else {
    if (!text || text === recent.partial) return null;
    recent.partial = text;
  }
  publish(`captions:${cfg.room_id}`, m.type, { text, words: [] });
  return { type: m.type, text };
}

function clear() {
  recent.finals = []; recent.finals_at = []; recent.partial = "";
  if (cfg.room_id) publish(`captions:${cfg.room_id}`, "clear", {});
}

// ------------------------------------------------------ transcripts --
// One file per room per sitting, plain text on this laptop only, deleted after the venue's hours.
let file = "";

function dir() {
  return path.join(app.getPath("userData"), "transcripts");
}

function write(text) {
  try {
    if (!file || !file.includes(`room${cfg.room_id}-`)) {
      fs.mkdirSync(dir(), { recursive: true });
      const d = new Date(), pad = (n) => String(n).padStart(2, "0");
      file = path.join(dir(), `room${cfg.room_id}-${d.getFullYear()}${pad(d.getMonth() + 1)}${pad(d.getDate())}-${pad(d.getHours())}${pad(d.getMinutes())}${pad(d.getSeconds())}.txt`);
    }
    const t = new Date().toTimeString().slice(0, 8);
    fs.appendFileSync(file, `[${t}] ${text}\n`);
  } catch (_) {}
}

// The latest transcript for this room: { name, text } or null.
function transcript() {
  try {
    const mine = fs.readdirSync(dir()).filter((n) => n.startsWith(`room${cfg.room_id}-`) && n.endsWith(".txt")).sort();
    if (!mine.length) return null;
    const name = mine[mine.length - 1];
    return { name, text: fs.readFileSync(path.join(dir(), name), "utf8") };
  } catch (_) {
    return null;
  }
}

function purge() {
  try {
    const cut = Date.now() - cfg.keep_hours * 3600 * 1000;
    for (const n of fs.readdirSync(dir())) {
      const p = path.join(dir(), n);
      if (fs.statSync(p).mtimeMs < cut) { fs.rmSync(p, { force: true }); if (p === file) file = ""; }
    }
  } catch (_) {}
}
setInterval(purge, 10 * 60 * 1000).unref();

// ---------------------------------------------------------- screens --
// The room's caption and timer screens can be fed straight from this laptop, so they keep
// going when the server can't be reached, and caption text never leaves the venue network.
// The pages themselves are the server's (kept on this laptop by offline-copy.js); only public
// readings are passed on, never the tech's sign-in.
const PASS = [
  /^\/static\/[\w./-]+$/, /^\/favicon\.ico$/,
  /^\/api\/captions\/\d+\/appearance$/, /^\/api\/timers\/\d+$/, /^\/api\/timers\/\d+\/cues$/,
  /^\/api\/timers-views$/, /^\/api\/timers-views\/look\/[\w%.-]+$/, /^\/api\/timers-views\/samples\/[\w.-]+\.html$/,
];
const PAGES = [/^\/captions\/\d+(\/overlay)?$/, /^\/timer\/\d+$/];
const DEFAULT_PORT = 8190;
let server = null, wss = null, port = 0;
const subs = new Map(); // socket -> Set(topic)

function publish(topic, type, data) {
  if (!wss) return;
  const text = JSON.stringify({ topic, type, data });
  for (const [ws, topics] of subs) if (topics.has(topic) && ws.readyState === 1) ws.send(text);
}

// The workspace passes on each timer state for its room (from the server, or worked out here offline).
function timer(state) {
  if (state && state.room_id != null) publish(`timer:${state.room_id}`, "timer", state);
}

function addresses() {
  const out = [];
  for (const list of Object.values(os.networkInterfaces())) {
    for (const a of list || []) if (a.family === "IPv4" && !a.internal) out.push(a.address);
  }
  return out;
}

function screensState() {
  return { on: !!server, port, addresses: addresses() };
}

// Starts the feed on the venue network. fetchServer(path) gets a public page or reading from the
// venue server (or the laptop's copy of it) as { status, type, body }.
function startScreens(fetchServer, wanted = DEFAULT_PORT) {
  if (server) return Promise.resolve(screensState());
  return new Promise((resolve, reject) => {
    const s = http.createServer((req, res) => serve(req, res, fetchServer).catch(() => { if (!res.headersSent) { res.writeHead(502); res.end(); } }));
    const w = new WebSocketServer({ noServer: true });
    s.on("upgrade", (req, sock, head) => {
      const url = new URL(req.url, "http://x");
      if (url.pathname !== "/ws") return sock.destroy();
      w.handleUpgrade(req, sock, head, (ws) => {
        const topics = new Set((url.searchParams.get("topics") || "").split(",").filter((t) => /^(timer|captions):\d+$/.test(t)));
        subs.set(ws, topics);
        ws.send(JSON.stringify({ type: "hello", topics: [...topics], signed_in: false }));
        ws.on("message", (raw) => {
          let m;
          try { m = JSON.parse(String(raw)); } catch (_) { return; }
          if (m.type === "ping") ws.send('{"type":"pong"}');
          if (m.type === "subscribe" && Array.isArray(m.topics)) {
            const more = m.topics.filter((t) => /^(timer|captions):\d+$/.test(t));
            more.forEach((t) => topics.add(t));
            ws.send(JSON.stringify({ type: "subscribed", topics: more }));
          }
        });
        ws.on("close", () => subs.delete(ws));
        ws.on("error", () => subs.delete(ws));
      });
    });
    s.once("error", reject);
    s.listen(wanted, "0.0.0.0", () => {
      server = s; wss = w; port = s.address().port;
      resolve(screensState());
    });
  });
}

function stopScreens() {
  if (!server) return Promise.resolve(screensState());
  for (const ws of subs.keys()) ws.terminate();
  subs.clear();
  wss.close();
  const s = server;
  server = null; wss = null; port = 0;
  return new Promise((resolve) => s.close(() => resolve(screensState())));
}

async function serve(req, res, fetchServer) {
  const url = new URL(req.url, "http://x");
  const p = url.pathname;
  if (req.method !== "GET" && req.method !== "HEAD") { res.writeHead(405); return res.end(); }
  if (p === "/" && cfg.room_id) { res.writeHead(302, { Location: `/captions/${cfg.room_id}` }); return res.end(); }
  const m = p.match(/^\/api\/captions\/(\d+)\/recent$/);
  if (m && Number(m[1]) === cfg.room_id) {
    const a = await fetchServer(`/api/captions/${cfg.room_id}/appearance`).catch(() => null);
    let appearance = null;
    try { appearance = a && a.status === 200 ? JSON.parse(a.body.toString("utf8")) : null; } catch (_) {}
    return json(res, { finals: recent.finals, finals_at: recent.finals_at, now: Date.now() / 1000, partial: recent.partial, live, appearance });
  }
  if (!PAGES.some((r) => r.test(p)) && !PASS.some((r) => r.test(p))) { res.writeHead(404); return res.end(); }
  const got = await fetchServer(p + url.search);
  res.writeHead(got.status, { "Content-Type": got.type || "application/octet-stream", "Cache-Control": "no-cache" });
  res.end(req.method === "HEAD" ? undefined : got.body);
}

function json(res, data) {
  res.writeHead(200, { "Content-Type": "application/json", "Cache-Control": "no-store" });
  res.end(JSON.stringify(data));
}

module.exports = {
  configure, setLive, heard, clear, transcript, timer,
  startScreens, stopScreens, screensState,
};
