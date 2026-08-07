const { app, BrowserWindow, ipcMain, shell } = require('electron');
const path = require('path');

let mainWindow = null;

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 440,
    height: 640,
    minWidth: 380,
    minHeight: 500,
    title: 'Dexter AI Super Assistant',
    frame: false,
    transparent: false,
    backgroundColor: '#090d16',
    alwaysOnTop: false,
    resizable: true,
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      nodeIntegration: false,
      contextIsolation: true
    }
  });

  const devUrl = 'http://localhost:3006';
  mainWindow.loadURL(devUrl).catch(() => {
    // If dev server loading fails initially, retry after 2 seconds
    setTimeout(() => {
      mainWindow.loadURL(devUrl);
    }, 2000);
  });

  mainWindow.on('closed', () => {
    mainWindow = null;
  });
}

// IPC Window Controls
ipcMain.on('window-minimize', () => {
  if (mainWindow) mainWindow.minimize();
});

ipcMain.on('window-toggle-maximize', () => {
  if (!mainWindow) return;
  if (mainWindow.isMaximized()) {
    mainWindow.unmaximize();
    mainWindow.setSize(440, 640);
  } else {
    mainWindow.maximize();
  }
});

ipcMain.on('window-set-compact', () => {
  if (!mainWindow) return;
  if (mainWindow.isMaximized()) {
    mainWindow.unmaximize();
  }
  mainWindow.setSize(440, 640);
});

ipcMain.on('window-set-expanded', () => {
  if (!mainWindow) return;
  mainWindow.setSize(1280, 800);
  mainWindow.center();
});

ipcMain.on('window-close', () => {
  if (mainWindow) mainWindow.close();
});

app.whenReady().then(() => {
  createWindow();

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit();
});
