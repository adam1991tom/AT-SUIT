// AT-SUIT Node: the tech workspace as a Windows app.
//
// The laptop enrols with the AT-SUIT server once; after that the app opens
// straight into the workspace (chat, room timer, captions mic, links, help),
// served by the server, in its own window with no browser around it.
//
// SILENCE IS A HARD RULE. The main laptop's screen is often on the projector,
// so this app must never make a sound and never take focus by itself:
//   - every window's audio output is muted (webContents.setAudioMuted)
//   - no Windows toast notifications (they can chime): pop-ups are our own
//     window, shown with showInactive() so it never steals focus
//   - web notifications are denied, and alert/confirm/prompt are replaced in
//     the page, because native dialogs can play the system sound
//   - pop-ups are off unless a tech turns them on for this laptop (the backup)
//   - no tray balloons, no taskbar flashing; updates install when the app starts
//     (if the tech left that on), when they press Update now, or when the app quits
//   - the overlay (the room timer over the slides) is click-through, never
//     focusable and shown without activating, so it can't take the keyboard
const path = require("path");
const os = require("os");
const { app, BrowserWindow, Menu, Tray, ipcMain, nativeImage, net, screen, session, shell } = require("electron");
const config = require("./config");

if (process.env.ATSUIT_NODE_USERDATA) app.setPath("userData", process.env.ATSUIT_NODE_USERDATA);

const AUTOSTART_ARG = "--autostart";
let conf = null;
let mainWin = null;
let setupWin = null;
let tray = null;
let quitting = false;
const popups = [];
let overlayWin = null;
let overlayUrl = "";

if (!app.requestSingleInstanceLock()) {
  app.quit();
} else {
  app.on("second-instance", () => openMain());
  boot();
}

function origin() {
  return conf && conf.server ? new URL(conf.server).origin : "";
}

function boot() {
  // Read settings before the app is ready: the server's origin has to be on
  // the command line so the page counts as secure and may use the microphone
  // over plain http, which avoids installing certificates on every laptop.
  // safeStorage isn't usable yet, so the token is read after ready.
  conf = config.load();
  if (conf.server) app.commandLine.appendSwitch("unsafely-treat-insecure-origin-as-secure", origin());
  app.commandLine.appendSwitch("disable-features", "HardwareMediaKeyHandling,MediaSessionService");
  app.setAppUserModelId("com.atsuit.node");
  Menu.setApplicationMenu(null);

  app.whenReady().then(async () => {
    lockDownSession();
    makeTray();
    ipc();
    watchDisplays();
    const token = config.getToken(conf);
    if (conf.server && token) {
      openMain();
      startUpdates();
      // An overlay left on comes back where it was, without waiting for the page.
      if (conf.overlay && conf.overlay.on) applyOverlay({}).catch(() => {});
      setInterval(followRoom, 60 * 1000);
      return;
    }
    const pre = config.preset();
    if (pre.server && pre.enrol_code) {
      const r = await enrol({ server: pre.server, code: pre.enrol_code, name: pre.name || os.hostname() });
      if (r.ok) return;
    }
    openSetup();
  });

  app.on("window-all-closed", (e) => e.preventDefault());
  app.on("before-quit", () => { quitting = true; });
}

// ------------------------------------------------------------- windows --
function lockDownSession() {
  const ses = session.defaultSession;
  const fromServer = (url) => { try { return !!origin() && new URL(url).origin === origin(); } catch (_) { return false; } };
  ses.setPermissionRequestHandler((wc, permission, cb, details) => {
    // The microphone (for captions) from our own server, nothing else.
    // Notifications are always refused: Windows toasts can play a sound.
    cb(permission === "media" && fromServer(details.requestingUrl || wc.getURL()));
  });
  ses.setPermissionCheckHandler((wc, permission, requestingOrigin) => permission === "media" && fromServer(requestingOrigin));
  // The overlay may show any page a tech chooses: it gets its own session
  // with no permissions at all (no mic, no notifications).
  const ov = session.fromPartition(OVERLAY_PARTITION);
  ov.setPermissionRequestHandler((wc, permission, cb) => cb(false));
  ov.setPermissionCheckHandler(() => false);
}

function silence(win) {
  win.webContents.setAudioMuted(true);
  win.webContents.on("did-start-navigation", () => win.webContents.setAudioMuted(true));
}

function openMain() {
  if (mainWin && !mainWin.isDestroyed()) {
    if (setupWin) return;
    mainWin.show();
    mainWin.focus();
    return;
  }
  if (!conf.server) return openSetup();
  mainWin = new BrowserWindow({
    width: 1280,
    height: 860,
    minWidth: 720,
    minHeight: 520,
    title: "AT-SUIT Node",
    icon: iconPath(),
    backgroundColor: "#0e1116",
    autoHideMenuBar: true,
    show: false,
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      sandbox: true,
      nodeIntegration: false,
      backgroundThrottling: false, // keep the captions mic and chat live when hidden
      spellcheck: false,
    },
  });
  silence(mainWin);
  keepToServer(mainWin);
  mainWin.once("ready-to-show", () => mainWin.show());
  mainWin.on("close", (e) => {
    // Closing hides to the tray so chat, pop-ups and the captions mic keep going.
    if (!quitting) { e.preventDefault(); mainWin.hide(); }
  });
  mainWin.webContents.on("render-process-gone", () => setTimeout(() => mainWin && !mainWin.isDestroyed() && mainWin.reload(), 2000));
  mainWin.webContents.on("did-fail-load", (_e, code, _desc, url, isMain) => {
    if (!isMain || code === -3) return;
    // Show our own "can't reach the server" page (not Chromium's), then try again.
    mainWin.loadFile(path.join(__dirname, "offline.html"), { query: { server: conf.server } }).catch(() => {});
    setTimeout(() => mainWin && !mainWin.isDestroyed() && mainWin.loadURL(`${conf.server}/node`).catch(() => {}), 5000);
  });
  // A proxy in front of the server answers 502/503 while it restarts: that isn't a failed load to
  // Chromium, so treat a 5xx page the same way and try again.
  mainWin.webContents.on("did-navigate", (_e, url, status) => {
    if (status >= 500 && url.startsWith(conf.server)) mainWin.webContents.emit("did-fail-load", {}, -100, `HTTP ${status}`, url, true);
  });
  mainWin.loadURL(`${conf.server}/node`);
}

// Windows popped out of the workspace (a chat, the timer, a screen preview):
// real windows of the app that can go on another monitor or stay on top.
const pops = new Map(); // frame name -> BrowserWindow
const POP = /^atsuit-pop-[\w-]{1,40}$/;

function keepToServer(win) {
  // Links to other tools open in the normal browser; this window stays on AT-SUIT.
  win.webContents.setWindowOpenHandler(({ url, frameName }) => {
    // The page fills a blank window itself and moves the live part into it.
    if (win === mainWin && POP.test(frameName) && (!url || url === "about:blank")) {
      return {
        action: "allow",
        overrideBrowserWindowOptions: {
          title: "AT-SUIT", icon: iconPath(), backgroundColor: "#0e1116", autoHideMenuBar: true, minWidth: 320, minHeight: 200,
          webPreferences: { contextIsolation: true, sandbox: true, nodeIntegration: false, backgroundThrottling: false, spellcheck: false },
        },
      };
    }
    if (/^https?:\/\//i.test(url)) shell.openExternal(url);
    return { action: "deny" };
  });
  win.webContents.on("did-create-window", (child, { frameName }) => {
    if (!POP.test(frameName || "")) return;
    pops.set(frameName, child);
    silence(child);
    child.webContents.setWindowOpenHandler(({ url }) => {
      if (/^https?:\/\//i.test(url)) shell.openExternal(url);
      return { action: "deny" };
    });
    child.webContents.on("will-navigate", (e) => e.preventDefault());
    child.on("closed", () => { if (pops.get(frameName) === child) pops.delete(frameName); });
  });
  win.webContents.on("will-navigate", (e, url) => {
    let same = false;
    try { same = new URL(url).origin === origin(); } catch (_) {}
    if (!same) {
      e.preventDefault();
      if (/^https?:\/\//i.test(url)) shell.openExternal(url);
    }
  });
}

function openSetup(message = "") {
  if (setupWin && !setupWin.isDestroyed()) { setupWin.show(); setupWin.focus(); return; }
  setupWin = new BrowserWindow({
    width: 560,
    height: 640,
    resizable: false,
    title: "Set up AT-SUIT Node",
    icon: iconPath(),
    backgroundColor: "#0e1116",
    webPreferences: { preload: path.join(__dirname, "setup-preload.js"), contextIsolation: true, sandbox: true },
  });
  silence(setupWin);
  setupWin.on("closed", () => { setupWin = null; });
  const pre = config.preset();
  setupWin.loadFile(path.join(__dirname, "setup.html"), {
    query: { server: conf.server || pre.server, code: pre.enrol_code, name: conf.name || pre.name || os.hostname(), message },
  });
}

// -------------------------------------------------------------- pop-ups --
const POPUP_W = 400;
const POPUP_H = 120;

function showPopup({ title, body, kind }) {
  if (!conf.popups) return false;
  while (popups.length >= 4) closePopup(popups[0]);
  const { workArea } = screen.getPrimaryDisplay();
  const p = new BrowserWindow({
    width: POPUP_W,
    height: POPUP_H,
    x: workArea.x + workArea.width - POPUP_W - 16,
    y: workArea.y + workArea.height - 16 - (POPUP_H + 10) * (popups.length + 1),
    frame: false,
    resizable: false,
    movable: false,
    minimizable: false,
    maximizable: false,
    fullscreenable: false,
    skipTaskbar: true,
    focusable: false, // never takes the keyboard from the slides
    alwaysOnTop: true,
    show: false,
    backgroundColor: kind === "urgent" ? "#7f1d1d" : "#1c2230",
    webPreferences: { preload: path.join(__dirname, "popup-preload.js"), contextIsolation: true, sandbox: true },
  });
  p.setAlwaysOnTop(true, "screen-saver"); // above full-screen PowerPoint
  silence(p);
  popups.push(p);
  p.on("closed", () => {
    const i = popups.indexOf(p);
    if (i >= 0) popups.splice(i, 1);
    restack();
  });
  p.loadFile(path.join(__dirname, "popup.html"), {
    query: { title: String(title || "AT-SUIT").slice(0, 120), body: String(body || "").slice(0, 300), kind: String(kind || "info") },
  });
  p.once("ready-to-show", () => p.showInactive());
  p.timer = setTimeout(() => closePopup(p), kind === "urgent" ? 20000 : 10000);
  return true;
}

function closePopup(p) {
  if (!p || p.isDestroyed()) return;
  clearTimeout(p.timer);
  p.destroy();
}

function restack() {
  const { workArea } = screen.getPrimaryDisplay();
  popups.forEach((p, i) => {
    if (!p.isDestroyed()) p.setPosition(workArea.x + workArea.width - POPUP_W - 16, workArea.y + workArea.height - 16 - (POPUP_H + 10) * (i + 1));
  });
}

// ------------------------------------------------------------- overlay --
// AT OVERLAY, built in: a click-through window over everything (full-screen
// slides included) showing the room timer or any page a tech picks. It can be
// turned on and off here (tray), from the workspace, or remotely from another
// laptop or Companion through the server (node command "overlay").
const OVERLAY_PARTITION = "atsuit-overlay";
const OVERLAY_POSITIONS = ["top-center", "bottom-right", "bottom-left", "top-right", "top-left", "bottom-bar", "top-bar"];
const OVERLAY_SIZES = { small: { w: 320, h: 110, bar: 56 }, medium: { w: 440, h: 150, bar: 80 }, large: { w: 600, h: 200, bar: 110 } };
const OVERLAY_MARGIN = 16;
const OVERLAY_DEFAULT = { on: false, url: "", position: "top-center", size: "medium", display: 0, opacity: 0.85, room_id: null };
const httpUrl = (u) => /^https?:\/\/[^\s]{1,1000}$/i.test(u);

function overlayConf(cfg, prev) {
  const pick = (k) => (cfg[k] !== undefined && cfg[k] !== null ? cfg[k] : prev[k] !== undefined ? prev[k] : OVERLAY_DEFAULT[k]);
  const num = (v, lo, hi, d) => (Number.isFinite(+v) ? Math.min(hi, Math.max(lo, +v)) : d);
  const url = String(pick("url") || "").trim();
  const room = parseInt(pick("room_id"), 10);
  return {
    on: !!pick("on"),
    url: httpUrl(url) ? url : "",
    position: OVERLAY_POSITIONS.includes(pick("position")) ? pick("position") : OVERLAY_DEFAULT.position,
    size: OVERLAY_SIZES[pick("size")] ? pick("size") : OVERLAY_DEFAULT.size,
    display: Math.round(num(pick("display"), 0, 8, 0)),
    opacity: num(pick("opacity"), 0.2, 1, OVERLAY_DEFAULT.opacity),
    room_id: room > 0 ? room : null,
  };
}

function overlayTarget(c) {
  if (c.url) return c.url;
  return c.room_id && conf.server ? `${conf.server}/timer/${c.room_id}?view=overlay` : "";
}

// Display 0 is the main display, then the others from left to right.
function displays() {
  const primary = screen.getPrimaryDisplay();
  const rest = screen.getAllDisplays().filter((d) => d.id !== primary.id).sort((a, b) => a.bounds.x - b.bounds.x || a.bounds.y - b.bounds.y);
  return [primary, ...rest];
}

function overlayBounds(c) {
  const all = displays();
  const wa = (all[c.display] || all[0]).workArea;
  const s = OVERLAY_SIZES[c.size];
  const m = OVERLAY_MARGIN;
  const w = Math.min(s.w, wa.width - 2 * m);
  const h = Math.min(s.h, wa.height - 2 * m);
  switch (c.position) {
    case "bottom-bar": return { x: wa.x, y: wa.y + wa.height - s.bar, width: wa.width, height: s.bar };
    case "top-bar": return { x: wa.x, y: wa.y, width: wa.width, height: s.bar };
    case "top-center": return { x: wa.x + Math.round((wa.width - w) / 2), y: wa.y + m, width: w, height: h };
    case "top-left": return { x: wa.x + m, y: wa.y + m, width: w, height: h };
    case "top-right": return { x: wa.x + wa.width - w - m, y: wa.y + m, width: w, height: h };
    case "bottom-left": return { x: wa.x + m, y: wa.y + wa.height - h - m, width: w, height: h };
    default: return { x: wa.x + wa.width - w - m, y: wa.y + wa.height - h - m, width: w, height: h };
  }
}

async function nodeFetch(method, url, body) {
  const token = config.getToken(conf);
  if (!conf.server || !token) return null;
  const req = net.fetch(`${conf.server}${url}`, {
    method,
    headers: { Authorization: `Node ${token}`, "Content-Type": "application/json" },
    body: body ? JSON.stringify(body) : undefined,
  }).then((r) => (r.ok ? r.json() : null));
  // Never hold up the overlay on a slow or missing server.
  return Promise.race([req, new Promise((r) => setTimeout(() => r(null), 4000))]).catch(() => null);
}

async function currentRoom() {
  const me = await nodeFetch("GET", "/api/nodes/me");
  return (me && me.room && me.room.id) || null;
}

// cfg: any of { on, url, position, size, display, opacity, room_id }; the
// rest keep their last values. url "" means this room's timer overlay.
async function applyOverlay(cfg) {
  if (cfg.url !== undefined && cfg.url !== null && String(cfg.url).trim() && !httpUrl(String(cfg.url).trim())) {
    return { ok: false, error: "The overlay URL must start with http:// or https://", ...overlayState() };
  }
  const c = overlayConf(cfg, conf.overlay || {});
  // The room timer follows the room this laptop is in today.
  if (c.on && !c.url && !cfg.room_id) c.room_id = (await currentRoom()) || c.room_id;
  let error = "";
  const target = overlayTarget(c);
  if (c.on && !target) { c.on = false; error = "Pick a room first, or give the overlay a URL"; }
  conf.overlay = c;
  config.save(conf);
  makeTray();
  if (c.on) openOverlay(c, target);
  else closeOverlay();
  reportOverlay(error);
  return { ok: !error, error, ...overlayState() };
}

function openOverlay(c, target) {
  const bounds = overlayBounds(c);
  if (!overlayWin || overlayWin.isDestroyed()) {
    const w = new BrowserWindow({
      ...bounds,
      title: "AT-SUIT Overlay",
      frame: false,
      transparent: true,
      backgroundColor: "#00000000",
      hasShadow: false,
      resizable: false,
      movable: false,
      minimizable: false,
      maximizable: false,
      fullscreenable: false,
      skipTaskbar: true,
      focusable: false, // never takes the keyboard from the slides
      alwaysOnTop: true,
      show: false,
      webPreferences: {
        partition: OVERLAY_PARTITION,
        contextIsolation: true,
        sandbox: true,
        nodeIntegration: false,
        backgroundThrottling: false,
        spellcheck: false,
      },
    });
    w.setAlwaysOnTop(true, "screen-saver"); // above full-screen PowerPoint
    w.setVisibleOnAllWorkspaces(true, { visibleOnFullScreen: true });
    w.setIgnoreMouseEvents(true); // clicks go to whatever is underneath
    silence(w);
    w.webContents.setWindowOpenHandler(() => ({ action: "deny" }));
    w.on("page-title-updated", (e) => e.preventDefault()); // always "AT-SUIT Overlay"
    w.webContents.on("render-process-gone", () => setTimeout(() => !w.isDestroyed() && w.reload(), 2000));
    w.webContents.on("did-fail-load", (_e, code, _desc, url, isMain) => {
      if (isMain && code !== -3) setTimeout(() => !w.isDestroyed() && overlayUrl && w.loadURL(overlayUrl).catch(() => {}), 5000);
    });
    w.webContents.on("did-navigate", (_e, url, status) => {
      if (status >= 500) setTimeout(() => !w.isDestroyed() && overlayUrl && w.loadURL(overlayUrl).catch(() => {}), 5000);
    });
    w.once("ready-to-show", () => !w.isDestroyed() && w.showInactive());
    // Windows can put a new frameless, transparent window where it likes when it first shows;
    // put it back where it was asked to go, and again once the page has loaded.
    const settle = () => { if (!w.isDestroyed() && conf.overlay) { w.setBounds(overlayBounds(overlayConf({}, conf.overlay))); reportOverlay(); } };
    w.on("show", () => { settle(); setTimeout(settle, 400); });
    w.webContents.on("did-finish-load", () => setTimeout(settle, 200));
    w.on("closed", () => { if (overlayWin === w) { overlayWin = null; overlayUrl = ""; } });
    overlayWin = w;
  }
  overlayWin.setBounds(bounds);
  overlayWin.setOpacity(c.opacity);
  if (overlayUrl !== target) {
    overlayUrl = target;
    overlayWin.loadURL(target).catch(() => {});
  } else if (!overlayWin.isVisible()) {
    overlayWin.showInactive();
  }
}

function closeOverlay() {
  if (overlayWin && !overlayWin.isDestroyed()) overlayWin.destroy();
  overlayWin = null;
  overlayUrl = "";
}

function overlayState() {
  const c = overlayConf({}, conf.overlay || {});
  const live = !!(overlayWin && !overlayWin.isDestroyed());
  return {
    ...c,
    on: live,
    target: live ? overlayUrl : "",
    bounds: live ? overlayWin.getBounds() : null,
    displays: displays().map((d, i) => ({ index: i, label: d.label || `Display ${i + 1}`, width: d.bounds.width, height: d.bounds.height, primary: i === 0 })),
  };
}

function reportOverlay(error = "") {
  const s = overlayState();
  nodeFetch("PUT", "/api/nodes/me/overlay", {
    on: s.on, url: s.url, target: s.target, position: s.position, size: s.size, display: s.display, opacity: s.opacity, room_id: s.room_id, error,
    bounds: s.bounds, version: app.getVersion(),
  }).catch(() => {});
}

function watchDisplays() {
  const again = () => {
    if (!overlayWin || overlayWin.isDestroyed() || !conf.overlay) return;
    overlayWin.setBounds(overlayBounds(overlayConf({}, conf.overlay)));
  };
  screen.on("display-added", again);
  screen.on("display-removed", again);
  screen.on("display-metrics-changed", again);
}

async function followRoom() {
  // A new day or a new room: the default overlay moves to that room's timer.
  const c = conf.overlay;
  if (!c || !c.on || c.url || !overlayWin) return;
  const room = await currentRoom();
  if (room && room !== c.room_id) applyOverlay({ room_id: room }).catch(() => {});
}

// ----------------------------------------------------------------- ipc --
function fromMain(e) {
  return mainWin && !mainWin.isDestroyed() && e.sender === mainWin.webContents && new URL(e.senderFrame.url).origin === origin();
}

function ipc() {
  ipcMain.handle("app:version", () => app.getVersion());
  ipcMain.handle("app:node-token", (e) => (fromMain(e) ? config.getToken(conf) : ""));
  ipcMain.handle("app:get-settings", (e) => (fromMain(e) ? { popups: !!conf.popups, name: conf.name, server: conf.server, update_on_launch: conf.update_on_launch !== false } : {}));
  ipcMain.handle("app:update-state", (e) => (fromMain(e) ? updateState() : {}));
  ipcMain.handle("app:update-check", (e) => (fromMain(e) ? checkUpdates() : {}));
  ipcMain.handle("app:update-install", (e) => (fromMain(e) ? installUpdate() : false));
  ipcMain.handle("app:set-settings", (e, s) => {
    if (!fromMain(e)) return false;
    if (typeof s?.popups === "boolean") conf.popups = s.popups;
    if (typeof s?.update_on_launch === "boolean") conf.update_on_launch = s.update_on_launch;
    config.save(conf);
    makeTray();
    return true;
  });
  ipcMain.handle("app:notify", (e, n) => (fromMain(e) ? showPopup(n || {}) : false));
  ipcMain.handle("app:overlay", (e, cfg) => (fromMain(e) ? applyOverlay(cfg && typeof cfg === "object" ? cfg : {}) : { ok: false }));
  ipcMain.handle("app:overlay-state", (e) => (fromMain(e) ? overlayState() : {}));
  ipcMain.handle("app:popout-top", (e, name, on) => {
    const w = fromMain(e) && pops.get(String(name));
    if (!w || w.isDestroyed()) return false;
    w.setAlwaysOnTop(!!on, "floating");
    return true;
  });
  ipcMain.handle("app:re-enrol", (e) => {
    if (!fromMain(e)) return false;
    // The server no longer knows this laptop (removed in Nodes): enrol again.
    config.setToken(conf, "");
    config.save(conf);
    openSetup("This laptop was removed from AT-SUIT. Enter the enrolment code to add it again.");
    if (mainWin) { mainWin.destroy(); mainWin = null; }
    return true;
  });
  ipcMain.on("popup:click", (e) => {
    const p = BrowserWindow.fromWebContents(e.sender);
    closePopup(p);
    openMain();
  });
  ipcMain.on("popup:dismiss", (e) => closePopup(BrowserWindow.fromWebContents(e.sender)));
  ipcMain.handle("setup:enrol", (e, form) => (setupWin && e.sender === setupWin.webContents ? enrol(form || {}) : { ok: false }));
}

async function enrol({ server, code, name }) {
  const base = config.normServer(server);
  if (!base) return { ok: false, error: "Enter the server address, like http://10.100.70.101:8180" };
  let res;
  try {
    res = await net.fetch(`${base}/api/nodes/enrol`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ code: String(code || "").trim().toUpperCase(), name: String(name || os.hostname()), kind: "tech" }),
    });
  } catch (err) {
    return { ok: false, error: `Can't reach ${base}. Check the address and that this laptop is on the venue network.` };
  }
  let data = {};
  try { data = await res.json(); } catch (_) {}
  if (!res.ok) return { ok: false, error: data.detail || `The server said ${res.status}` };
  conf.server = base;
  conf.name = data.name || name;
  config.setToken(conf, data.token);
  config.save(conf);
  if (app.isPackaged) app.setLoginItemSettings({ openAtLogin: true, args: [AUTOSTART_ARG] });
  // Restart so the server's address is trusted for the microphone.
  setTimeout(() => { quitting = true; app.relaunch(); app.exit(0); }, 300);
  return { ok: true };
}

// ---------------------------------------------------------------- tray --
function iconPath() {
  return path.join(__dirname, "..", "build", "icon.png");
}

function makeTray() {
  // The simplified mark drawn for 16 px, with the 32 px one for high-DPI screens.
  const img = nativeImage.createFromPath(path.join(__dirname, "..", "build", "tray-16.png"));
  img.addRepresentation({ scaleFactor: 2, buffer: require("fs").readFileSync(path.join(__dirname, "..", "build", "tray-32.png")) });
  if (!tray) {
    tray = new Tray(img);
    tray.setToolTip(`AT-SUIT Node ${app.getVersion()}`);
    tray.on("click", () => openMain());
  }
  const login = app.getLoginItemSettings().openAtLogin;
  const upItem = upd.state === "ready" ? { label: `Restart to update to ${upd.version}`, click: () => installUpdate() }
    : upd.state === "downloading" ? { label: `Downloading ${upd.version}…`, enabled: false }
    : upd.state === "installing" ? { label: "Updating…", enabled: false }
    : { label: "Check for updates", enabled: !!updater, click: () => checkUpdates() };
  tray.setContextMenu(Menu.buildFromTemplate([
    { label: "Open workspace", click: () => openMain() },
    { type: "separator" },
    { label: `AT-SUIT Node ${app.getVersion()}`, enabled: false },
    upItem,
    { type: "separator" },
    // Set in the workspace when the tech starts: Main PC never pops up, Backup PC pops up silently.
    { label: conf.popups ? "Backup PC: silent pop-ups" : "Main PC: no pop-ups", enabled: false },
    {
      label: "Overlay on/off",
      type: "checkbox",
      checked: !!(conf.overlay && conf.overlay.on),
      enabled: !!conf.server,
      click: (item) => applyOverlay({ on: item.checked }).catch(() => {}),
    },
    {
      label: "Start with Windows",
      type: "checkbox",
      checked: login,
      enabled: app.isPackaged,
      click: (item) => app.setLoginItemSettings({ openAtLogin: item.checked, args: [AUTOSTART_ARG] }),
    },
    { type: "separator" },
    { label: "Quit AT-SUIT Node", click: () => { quitting = true; app.quit(); } },
  ]));
}

// ------------------------------------------------------------- updates --
// Updates come from this venue's AT-SUIT server (which fetches each release's app from GitHub,
// or an admin uploads it in Laptops & screens), so laptops need no internet; only when the server
// has no app at all does a laptop try GitHub itself. The app checks a few seconds after it starts and every few hours; a newer
// version downloads quietly. It installs:
//   - straight away when it is found as the app starts (update_on_launch, on unless the
//     tech turns it off), so a laptop switched on in the morning is up to date before the show;
//   - when the tech presses Update now (This laptop, or the tray);
//   - otherwise when the app next quits, never in the middle of a show.
let updater = null;
const LAUNCH_WINDOW_MS = 3 * 60 * 1000;
const startedAt = Date.now();
const upd = { state: "off", version: "", percent: 0, error: "", checked_at: 0, source: "the server" };

function updateState() {
  return { ...upd, current: app.getVersion(), on_launch: conf.update_on_launch !== false };
}

function setUpd(patch, quiet) {
  Object.assign(upd, patch);
  if (mainWin && !mainWin.isDestroyed()) mainWin.webContents.send("app:update", updateState());
  if (!quiet) makeTray(); // not on every download-progress tick
}

function startUpdates() {
  if (!app.isPackaged || process.env.ATSUIT_NODE_NO_UPDATES) return;
  try { ({ autoUpdater: updater } = require("electron-updater")); } catch (_) { return; }
  updater.setFeedURL({ provider: "generic", url: `${conf.server}/api/nodes/app/` });  // pickFeed() may change it
  updater.autoDownload = true;
  updater.autoInstallOnAppQuit = true;
  updater.logger = null;
  updater.on("checking-for-update", () => setUpd({ state: "checking", error: "" }));
  updater.on("update-not-available", () => setUpd({ state: "none", checked_at: Date.now() }));
  updater.on("update-available", (i) => setUpd({ state: "downloading", version: i.version, percent: 0, checked_at: Date.now() }));
  updater.on("download-progress", (p) => setUpd({ percent: Math.round(p.percent || 0) }, true));
  updater.on("error", (e) => setUpd({ state: "error", error: String((e && e.message) || e).split("\n")[0].slice(0, 200) }));
  updater.on("update-downloaded", (i) => {
    setUpd({ state: "ready", version: i.version, percent: 100 });
    if (conf.update_on_launch !== false && Date.now() - startedAt < LAUNCH_WINDOW_MS) installUpdate(5000);
  });
  setUpd({ state: "idle" });
  const first = Number(process.env.ATSUIT_NODE_UPDATE_CHECK_S);
  setTimeout(checkUpdates, (first > 0 ? first : 5) * 1000);
  setInterval(checkUpdates, 4 * 60 * 60 * 1000);
}

// Where the update comes from: this venue's server, or, when the server has no app published
// and updates are on there, the newest GitHub release (laptops with internet only).
async function pickFeed() {
  let info = null;
  try {
    const r = await net.fetch(`${conf.server}/api/nodes/app`, { cache: "no-store" });
    if (r.ok) info = await r.json();
  } catch (_) { /* server unreachable: try it anyway, the check reports the error */ }
  const github = info && !info.version && /^[\w.-]+\/[\w.-]+$/.test(info.github || "") ? info.github : "";
  const url = github ? `https://github.com/${github}/releases/latest/download/` : `${conf.server}/api/nodes/app/`;
  updater.setFeedURL({ provider: "generic", url });
  upd.source = github ? "GitHub" : "the server";
}

function checkUpdates() {
  if (!updater) return Promise.resolve(updateState());
  if (["checking", "downloading", "ready", "installing"].includes(upd.state)) return Promise.resolve(updateState());
  return pickFeed().then(() => updater.checkForUpdates()).then(() => updateState(), (e) => {
    setUpd({ state: "error", error: String((e && e.message) || e).split("\n")[0].slice(0, 200) });
    return updateState();
  });
}

// Install the downloaded update and start the new version. The workspace shows a notice for
// `afterMs` first, so the tech knows why the app is about to close and come back.
function installUpdate(afterMs = 1500) {
  if (!updater || upd.state !== "ready") return false;
  setUpd({ state: "installing" });
  setTimeout(() => { quitting = true; updater.quitAndInstall(true, true); }, afterMs);
  return true;
}

module.exports = { showPopup, applyOverlay };
