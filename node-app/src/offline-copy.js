// The workspace's own copy on this laptop, so the app opens and runs the show when the
// server can't be reached (the server is down, restarting, or the laptop is off its network).
//
// Every request from the app's windows to the venue's server still goes to the server first.
// When the server answers, the workspace page, its files and the few readings it needs to start
// are kept on this laptop; when it doesn't, those are answered from the kept copy instead.
// Only these are kept: never chat, help calls, captions or anything else. The page keeps working
// the timer itself while offline (offline-timer.js) and catches the server up when it's back.
const crypto = require("crypto");
const fs = require("fs");
const path = require("path");
const { app, net } = require("electron");

const KEPT = [
  /^\/node$/, /^\/static\/[\w./-]+$/, /^\/favicon\.ico$/,
  /^\/api\/nodes\/me$/, /^\/api\/auth\/me$/, /^\/api\/bootstrap$/, /^\/api\/public\/branding$/, /^\/api\/workspace\/layout$/,
  /^\/api\/timers\/\d+$/, /^\/api\/timers\/\d+\/cues$/, /^\/api\/timers-views$/, /^\/api\/timers-quick-messages$/,
  /^\/api\/captions\/\d+\/settings$/, /^\/api\/ai$/,
];
const TIMER = /^\/api\/timers\/\d+$/;
const WAIT_MS = 6000; // how long a kept request waits for the server before the copy is used
const MAX_BYTES = 8 * 1024 * 1024;

let origin = () => "";

function dir() {
  return path.join(app.getPath("userData"), "offline-copy");
}

function fileFor(key) {
  return path.join(dir(), crypto.createHash("sha1").update(key).digest("hex"));
}

const kept = (url) => url.origin === origin() && KEPT.some((r) => r.test(url.pathname));

// serverOrigin() gives the venue server's origin. Must be called once the app is ready.
function start(ses, serverOrigin) {
  origin = serverOrigin;
  for (const scheme of ["http", "https"]) ses.protocol.handle(scheme, (req) => handle(ses, req));
}

async function handle(ses, req) {
  let url;
  try { url = new URL(req.url); } catch (_) { return ses.fetch(req, { bypassCustomProtocolHandlers: true }); }
  if (req.method !== "GET" || !kept(url)) return ses.fetch(req, { bypassCustomProtocolHandlers: true });
  const key = url.pathname + url.search;
  try {
    const res = await ses.fetch(req, { bypassCustomProtocolHandlers: true, signal: AbortSignal.timeout(WAIT_MS) });
    // A proxy in front of a restarting server answers 502/503/504: use the copy for those too.
    if ([502, 503, 504].includes(res.status)) {
      const copy = await fromCopy(key, url);
      if (copy) return copy;
    }
    if (res.status === 200) return keep(key, res);
    return res;
  } catch (err) {
    const copy = await fromCopy(key, url);
    if (copy) return copy;
    throw err;
  }
}

// Keeps a copy of a good answer, and hands the page the same answer.
async function keep(key, res) {
  const body = Buffer.from(await res.arrayBuffer());
  const headers = {};
  for (const h of ["content-type", "cache-control", "etag", "last-modified"]) if (res.headers.get(h)) headers[h] = res.headers.get(h);
  if (body.length <= MAX_BYTES) {
    const f = fileFor(key);
    fs.promises.mkdir(dir(), { recursive: true })
      .then(() => fs.promises.writeFile(`${f}.tmp`, Buffer.concat([Buffer.from(JSON.stringify({ key, at: Date.now(), headers }) + "\n"), body])))
      .then(() => fs.promises.rename(`${f}.tmp`, f))
      .catch(() => {});
  }
  // The body is already decoded here, so it goes back without the server's encoding and length.
  const out = new Headers(res.headers);
  out.delete("content-encoding");
  out.delete("content-length");
  return new Response(body, { status: res.status, statusText: res.statusText, headers: out });
}

async function fromCopy(key, url) {
  let raw;
  try { raw = await fs.promises.readFile(fileFor(key)); } catch (_) { return null; }
  const nl = raw.indexOf(10);
  let meta;
  try { meta = JSON.parse(raw.subarray(0, nl).toString("utf8")); } catch (_) { return null; }
  if (meta.key !== key) return null;
  let body = raw.subarray(nl + 1);
  if (TIMER.test(url.pathname)) body = Buffer.from(JSON.stringify(timerNow(JSON.parse(body.toString("utf8")), Date.now() - meta.at)));
  return new Response(body, { status: 200, headers: { ...meta.headers, "cache-control": "no-store", "x-atsuit-kept": String(meta.at) } });
}

// A kept timer reading is from when it was kept: move a running timer on to now.
function timerNow(s, gone) {
  if (s.running) s.remaining_ms -= gone;
  if (s.secondary && s.secondary.running) s.secondary.remaining_ms -= gone;
  if (typeof s.clock_ms === "number") s.clock_ms = (s.clock_ms + gone) % 86400000;
  if (typeof s.server_time === "number") s.server_time += gone / 1000;
  return s;
}

// Forget everything kept (when the laptop leaves the venue: re-enrol).
function clear() {
  fs.rmSync(dir(), { recursive: true, force: true });
}

module.exports = { start, clear };
