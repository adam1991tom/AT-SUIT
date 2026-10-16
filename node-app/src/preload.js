// Bridge between the AT-SUITE server's /node page and this app. Only the
// server's own page gets answers (checked again in main.js).
const { contextBridge, ipcRenderer } = require("electron");

// Native alert/confirm/prompt boxes can play the Windows system sound, so the
// page never gets them inside this app.
contextBridge.executeInMainWorld({
  func: () => {
    window.alert = (m) => console.warn("alert() is disabled in AT-SUITE Node:", m);
    window.confirm = () => false;
    window.prompt = () => null;
  },
});

contextBridge.exposeInMainWorld("atsuitApp", {
  version: () => ipcRenderer.invoke("app:version"),
  // Updates from this venue's server: updateState() → { state, current, version, percent, error,
  // checked_at, on_launch }; state is off|idle|checking|none|downloading|ready|installing|error.
  updateState: () => ipcRenderer.invoke("app:update-state"),
  checkUpdate: () => ipcRenderer.invoke("app:update-check"),
  installUpdate: () => ipcRenderer.invoke("app:update-install"),
  onUpdate: (cb) => ipcRenderer.on("app:update", (_e, st) => cb(st)),
  nodeToken: () => ipcRenderer.invoke("app:node-token"),
  getSettings: () => ipcRenderer.invoke("app:get-settings"),
  setSettings: (s) => ipcRenderer.invoke("app:set-settings", s),
  notify: (n) => ipcRenderer.invoke("app:notify", n),
  // Maximise the app's window (after a tech signs in).
  maximise: () => ipcRenderer.invoke("app:maximise"),
  reEnrol: () => ipcRenderer.invoke("app:re-enrol"),
  // The click-through overlay: overlay({ on, url, position, size, display, opacity, room_id })
  // changes any of them and resolves to the new state; overlayState() just reads it.
  overlay: (cfg) => ipcRenderer.invoke("app:overlay", cfg),
  overlayState: () => ipcRenderer.invoke("app:overlay-state"),
  // Task-manager numbers for the heartbeat: system() → { cpu, mem_total, mem_free, app_mem, disk, net, displays, … }
  system: () => ipcRenderer.invoke("app:system"),
  // Small JPEGs of each screen, only sent when the venue turns snapshots on and a manager is looking.
  snapshot: () => ipcRenderer.invoke("app:snapshot"),
  // Captions made on this laptop (see captions.js): prepare() fetches the venue's model from the
  // server once and loads it; then start(audio), feed(Int16 PCM at 16 kHz), stop(). onCaptions gets
  // { type: partial|final|level|state|error, ... }.
  // The room timer worked out on this laptop while the server can't be reached (offline-timer.js).
  timerLocal: (state) => ipcRenderer.send("timer:local", state),
  // The laptop bubble: captions kept on this laptop and its own screen feed (bubble.js).
  // bubble.configure({ room_id, private, options, vocabulary, keep_hours }); bubble.timer(state) for
  // the screens; transcript() → { name, lines } | null; saveTranscript() asks where to save it.
  bubble: {
    configure: (c) => ipcRenderer.send("bubble:configure", c),
    timer: (state) => ipcRenderer.send("bubble:timer", state),
    clear: () => ipcRenderer.send("bubble:clear"),
    transcript: () => ipcRenderer.invoke("bubble:transcript"),
    saveTranscript: () => ipcRenderer.invoke("bubble:save-transcript"),
  },
  // screens() → { on, port, addresses }; setScreens(on) turns the feed on or off.
  screens: () => ipcRenderer.invoke("screens:state"),
  setScreens: (on) => ipcRenderer.invoke("screens:set", on),
  captions: {
    prepare: () => ipcRenderer.invoke("captions:prepare"),
    state: () => ipcRenderer.invoke("captions:state"),
    start: (audio) => ipcRenderer.send("captions:start", audio),
    audio: (audio) => ipcRenderer.send("captions:audio", audio),
    feed: (pcm) => ipcRenderer.send("captions:feed", pcm),
    stop: () => ipcRenderer.send("captions:stop"),
    onEvent: (cb) => ipcRenderer.on("captions:event", (_e, m) => cb(m)),
  },
  // A window popped out of the workspace: keep it above everything else.
  popoutOnTop: (name, on) => ipcRenderer.invoke("app:popout-top", name, on),
});
