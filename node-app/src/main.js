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
//   - no tray balloons, no taskbar flashing, updates install only on quit
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
    const token = config.getToken(conf);
    if (conf.server && token) {
      openMain();
      startUpdates();
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
    if (isMain && code !== -3) setTimeout(() => mainWin && !mainWin.isDestroyed() && mainWin.loadURL(`${conf.server}/node`), 5000);
  });
  mainWin.loadURL(`${conf.server}/node`);
}

function keepToServer(win) {
  // Links to other tools open in the normal browser; this window stays on AT-SUIT.
  win.webContents.setWindowOpenHandler(({ url }) => {
    if (/^https?:\/\//i.test(url)) shell.openExternal(url);
    return { action: "deny" };
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

// ----------------------------------------------------------------- ipc --
function fromMain(e) {
  return mainWin && !mainWin.isDestroyed() && e.sender === mainWin.webContents && new URL(e.senderFrame.url).origin === origin();
}

function ipc() {
  ipcMain.handle("app:version", () => app.getVersion());
  ipcMain.handle("app:node-token", (e) => (fromMain(e) ? config.getToken(conf) : ""));
  ipcMain.handle("app:get-settings", (e) => (fromMain(e) ? { popups: !!conf.popups, name: conf.name, server: conf.server } : {}));
  ipcMain.handle("app:set-settings", (e, s) => {
    if (!fromMain(e)) return false;
    if (typeof s?.popups === "boolean") conf.popups = s.popups;
    config.save(conf);
    makeTray();
    return true;
  });
  ipcMain.handle("app:notify", (e, n) => (fromMain(e) ? showPopup(n || {}) : false));
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
  const img = nativeImage.createFromPath(iconPath()).resize({ width: 16, height: 16 });
  if (!tray) {
    tray = new Tray(img);
    tray.setToolTip("AT-SUIT Node");
    tray.on("click", () => openMain());
  }
  const login = app.getLoginItemSettings().openAtLogin;
  tray.setContextMenu(Menu.buildFromTemplate([
    { label: "Open workspace", click: () => openMain() },
    { type: "separator" },
    {
      label: "Silent pop-ups (backup laptop only)",
      type: "checkbox",
      checked: !!conf.popups,
      click: (item) => {
        conf.popups = item.checked;
        config.save(conf);
        if (mainWin && !mainWin.isDestroyed()) mainWin.webContents.send("app:settings-changed");
      },
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
function startUpdates() {
  if (!app.isPackaged || process.env.ATSUIT_NODE_NO_UPDATES) return;
  let autoUpdater;
  try { ({ autoUpdater } = require("electron-updater")); } catch (_) { return; }
  // Updates come from this venue's AT-SUIT server (Admin → Node setup), not
  // the internet. They download quietly and install when the app next quits,
  // never in the middle of a show.
  autoUpdater.setFeedURL({ provider: "generic", url: `${conf.server}/api/nodes/app/` });
  autoUpdater.autoDownload = true;
  autoUpdater.autoInstallOnAppQuit = true;
  autoUpdater.logger = null;
  autoUpdater.on("error", () => {});
  const check = () => autoUpdater.checkForUpdates().catch(() => {});
  setTimeout(check, 60 * 1000);
  setInterval(check, 4 * 60 * 60 * 1000);
}

module.exports = { showPopup };
