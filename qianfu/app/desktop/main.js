// Electron 主进程：拉起 Python 后端，等它健康后再开窗口。
// 壳本身不写业务逻辑——所有功能都在 /api/v1 后面，
// 这样将来换成 Tauri、或者让 Agent 直接调接口，都不用改这里。

const { app, BrowserWindow, shell, dialog } = require("electron");
const { spawn } = require("node:child_process");
const path = require("node:path");

const PORT = Number(process.env.XINGE_PORT || 8722);
const BASE = `http://127.0.0.1:${PORT}`;
const ROOT = path.resolve(__dirname, "..", "..");   // -> qianfu/
const PYTHON = process.env.XINGE_PYTHON || "python";

let backend = null;
let win = null;

function startBackend() {
  backend = spawn(PYTHON, ["-m", "app.server", "--port", String(PORT)], {
    cwd: ROOT,
    env: { ...process.env, PYTHONIOENCODING: "utf-8" },
    stdio: ["ignore", "pipe", "pipe"],
  });

  backend.stdout.on("data", (d) => process.stdout.write("[py] " + d));
  backend.stderr.on("data", (d) => process.stderr.write("[py] " + d));
  backend.on("exit", (code) => {
    if (code !== 0 && code !== null) {
      dialog.showErrorBox("后端已退出", `Python 进程退出码 ${code}`);
    }
  });
}

async function waitForHealth(timeoutMs = 30000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    try {
      const res = await fetch(`${BASE}/api/v1/health`);
      if (res.ok) return true;
    } catch {
      /* 还没起来 */
    }
    await new Promise((r) => setTimeout(r, 400));
  }
  return false;
}

async function createWindow() {
  win = new BrowserWindow({
    width: 1180,
    height: 780,
    minWidth: 900,
    minHeight: 600,
    title: "信鸽 SJTU",
    backgroundColor: "#f7f8fa",
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });

  win.loadURL(`${BASE}/static/index.html`);

  // 外链用系统浏览器打开，不要在应用里跳走
  win.webContents.setWindowOpenHandler(({ url }) => {
    shell.openExternal(url);
    return { action: "deny" };
  });
}

app.whenReady().then(async () => {
  startBackend();

  const healthy = await waitForHealth();
  if (!healthy) {
    dialog.showErrorBox(
      "后端启动失败",
      "30 秒内没能连上本地服务。\n\n请检查 Python 是否安装、依赖是否齐全：\n" +
        "cd qianfu && python -m app.server"
    );
    app.quit();
    return;
  }

  await createWindow();

  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});

app.on("window-all-closed", () => {
  if (backend) backend.kill();
  app.quit();
});

app.on("before-quit", () => {
  if (backend) backend.kill();
});
