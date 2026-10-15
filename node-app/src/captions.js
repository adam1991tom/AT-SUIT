// Captions made on this laptop. The tech's mic is turned into text here, by the same offline
// speech model the server uses, and only the text goes to the server. The model comes from
// the venue's own server once (nothing from the internet), and runs in a separate process so
// the workspace never stutters while it listens.
const fs = require("fs");
const path = require("path");
const { app, net, utilityProcess } = require("electron");

let st = { state: "idle", detail: "", progress: 0 };
let worker = null;
let loadedKey = "";
let onEvent = () => {};
let preparing = null;
let quitting = false;

function modelsDir() {
  return path.join(app.getPath("userData"), "models");
}

function set(changes) {
  st = { ...st, ...changes };
  onEvent({ type: "state", ...st });
}

async function download(url, token, dest, size, progress) {
  const r = await net.fetch(url, { headers: { Authorization: `Node ${token}` }, cache: "no-store" });
  if (!r.ok) throw new Error(`The server said ${r.status} for ${path.basename(dest)}`);
  const part = `${dest}.part`;
  const out = fs.createWriteStream(part);
  let got = 0;
  try {
    for await (const chunk of r.body) {
      got += chunk.length;
      if (!out.write(chunk)) await new Promise((res) => out.once("drain", res));
      progress(got);
    }
  } finally {
    await new Promise((res) => out.end(res));
  }
  if (size && got !== size) throw new Error(`${path.basename(dest)} arrived incomplete`);
  fs.renameSync(part, dest);
}

// Make sure this laptop has the venue's model and vocabulary. Resolves to
// { ok, state, detail, progress }; ok is false while the server is still getting the model.
async function prepare(server, token) {
  if (preparing) return preparing;
  preparing = (async () => {
    try {
      const r = await net.fetch(`${server}/api/nodes/captions/model`, { headers: { Authorization: `Node ${token}` }, cache: "no-store" });
      if (!r.ok) throw new Error(`The server said ${r.status}`);
      const m = await r.json();
      if (m.state !== "ready") {
        set({ state: m.state === "off" ? "off" : "waiting", detail: m.detail || "The server is getting the speech model ready", progress: m.progress || 0 });
        return { ok: false, ...st };
      }
      const dir = path.join(modelsDir(), m.model.replace(/[^\w.-]/g, "_"));
      fs.mkdirSync(dir, { recursive: true });
      let keyFile = "";
      try { keyFile = fs.readFileSync(path.join(dir, "key.txt"), "utf8"); } catch (_) {}
      const need = m.files.filter((f) => {
        const p = path.join(dir, f.name);
        if (!fs.existsSync(p)) return true;
        if (f.small) return keyFile !== m.key;
        return fs.statSync(p).size !== f.size;
      });
      const total = need.reduce((n, f) => n + f.size, 0) || 1;
      let done = 0;
      if (need.length) set({ state: "downloading", detail: "Getting the speech model from the server (once)", progress: 0 });
      for (const f of need) {
        await download(`${server}/api/nodes/captions/model/${encodeURIComponent(f.name)}`, token, path.join(dir, f.name), f.size,
          (got) => { const p = Math.round(((done + got) / total) * 100); if (p !== st.progress) set({ progress: p }); });
        done += f.size;
      }
      fs.writeFileSync(path.join(dir, "key.txt"), m.key);
      await load(dir, m.key, m.hotwords_score);
      return { ok: true, ...st };
    } catch (e) {
      set({ state: "error", detail: String(e.message || e) });
      return { ok: false, ...st };
    } finally {
      preparing = null;
    }
  })();
  return preparing;
}

function startWorker() {
  if (worker) return worker;
  worker = utilityProcess.fork(path.join(__dirname, "captions-worker.js"), [], { serviceName: "AT-SUIT captions" });
  worker.on("message", (m) => {
    if (m.type === "loaded") { loadedKey = m.key; set({ state: "ready", detail: "", progress: 100 }); }
    else if (m.type === "load-error") set({ state: "error", detail: m.detail });
    else onEvent(m);
  });
  worker.on("exit", () => {
    worker = null;
    loadedKey = "";
    if (quitting) return;
    if (st.state !== "idle") set({ state: "error", detail: "The captions engine stopped" });
    onEvent({ type: "error", detail: "The captions engine stopped" });
  });
  return worker;
}

function load(dir, key, hotwordsScore) {
  if (loadedKey === key && worker) { set({ state: "ready", detail: "", progress: 100 }); return Promise.resolve(); }
  set({ state: "loading", detail: "Loading the speech model", progress: 100 });
  return new Promise((resolve) => {
    const w = startWorker();
    const done = (m) => { if (m.type === "loaded" || m.type === "load-error") { w.off("message", done); resolve(); } };
    w.on("message", done);
    w.postMessage({ type: "load", dir, key, hotwordsScore });
  });
}

module.exports = {
  prepare,
  state: () => st,
  ready: () => st.state === "ready" && !!worker,
  setListener: (fn) => { onEvent = fn; },
  start: (audio) => worker && worker.postMessage({ type: "start", audio }),
  audio: (audio) => worker && worker.postMessage({ type: "audio", audio }),
  feed: (pcm) => worker && worker.postMessage({ type: "pcm", pcm }),
  stop: () => worker && worker.postMessage({ type: "stop" }),
  quit: () => { quitting = true; if (worker) worker.kill(); },
};
