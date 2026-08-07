const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('electronAPI', {
  minimize: () => ipcRenderer.send('window-minimize'),
  toggleMaximize: () => ipcRenderer.send('window-toggle-maximize'),
  setCompact: () => ipcRenderer.send('window-set-compact'),
  setExpanded: () => ipcRenderer.send('window-set-expanded'),
  close: () => ipcRenderer.send('window-close')
});
