const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("popup", {
  click: () => ipcRenderer.send("popup:click"),
  dismiss: () => ipcRenderer.send("popup:dismiss"),
});
