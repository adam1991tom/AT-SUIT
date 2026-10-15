const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("setup", {
  prefill: () => ipcRenderer.invoke("setup:prefill"),
  enrol: (form) => ipcRenderer.invoke("setup:enrol", form),
});
