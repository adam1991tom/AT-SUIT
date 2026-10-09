const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("setup", {
  enrol: (form) => ipcRenderer.invoke("setup:enrol", form),
});
