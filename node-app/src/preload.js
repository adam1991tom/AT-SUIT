// Bridge between the AT-SUIT server's /node page and this app. Only the
// server's own page gets answers (checked again in main.js).
const { contextBridge, ipcRenderer } = require("electron");

// Native alert/confirm/prompt boxes can play the Windows system sound, so the
// page never gets them inside this app.
contextBridge.executeInMainWorld({
  func: () => {
    window.alert = (m) => console.warn("alert() is disabled in AT-SUIT Node:", m);
    window.confirm = () => false;
    window.prompt = () => null;
  },
});

contextBridge.exposeInMainWorld("atsuitApp", {
  version: () => ipcRenderer.invoke("app:version"),
  nodeToken: () => ipcRenderer.invoke("app:node-token"),
  getSettings: () => ipcRenderer.invoke("app:get-settings"),
  setSettings: (s) => ipcRenderer.invoke("app:set-settings", s),
  notify: (n) => ipcRenderer.invoke("app:notify", n),
  reEnrol: () => ipcRenderer.invoke("app:re-enrol"),
  // The click-through overlay: overlay({ on, url, position, size, display, opacity, room_id })
  // changes any of them and resolves to the new state; overlayState() just reads it.
  overlay: (cfg) => ipcRenderer.invoke("app:overlay", cfg),
  overlayState: () => ipcRenderer.invoke("app:overlay-state"),
  // A window popped out of the workspace: keep it above everything else.
  popoutOnTop: (name, on) => ipcRenderer.invoke("app:popout-top", name, on),
});
