const { app, BrowserWindow, ipcMain, shell } = require('electron');
const path = require('path');
const fs = require('fs');

// Disable GPU hardware acceleration to prevent invisible/black window issues on Windows
app.disableHardwareAcceleration();
app.commandLine.appendSwitch('disable-gpu');
app.commandLine.appendSwitch('no-sandbox');

let mainWindow = null;

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 460,
    height: 680,
    minWidth: 380,
    minHeight: 500,
    title: 'Dexter AI Super Assistant',
    frame: true,
    backgroundColor: '#090d16',
    alwaysOnTop: true,
    resizable: true,
    show: true,
    webPreferences: {
      preload: path.join(__dirname, 'preload.cjs'),
      nodeIntegration: false,
      contextIsolation: true
    }
  });

  const distPath = path.join(__dirname, '../dist/index.html');
  const devUrl = 'http://localhost:3006';

  if (fs.existsSync(distPath)) {
    mainWindow.loadFile(distPath).catch((err) => {
      console.log('Error loading dist file, loading devUrl:', err);
      mainWindow.loadURL(devUrl);
    });
  } else {
    mainWindow.loadURL(devUrl).catch(() => {
      setTimeout(() => mainWindow.loadURL(devUrl), 1500);
    });
  }

  // Focus and bring to front on startup
  mainWindow.once('ready-to-show', () => {
    mainWindow.show();
    mainWindow.focus();
  });

  // Release alwaysOnTop after 3 seconds so user can move/minimize freely
  setTimeout(() => {
    if (mainWindow && !mainWindow.isDestroyed()) {
      mainWindow.setAlwaysOnTop(false);
    }
  }, 3000);

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
    mainWindow.setSize(460, 680);
  } else {
    mainWindow.maximize();
  }
});

ipcMain.on('window-set-compact', () => {
  if (!mainWindow) return;
  if (mainWindow.isMaximized()) {
    mainWindow.unmaximize();
  }
  mainWindow.setSize(460, 680);
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
