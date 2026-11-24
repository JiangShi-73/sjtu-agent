// Electron 主进程：拉起 Python 后端，等它健康后再开窗口。
// 壳本身不写业务逻辑——所有功能都在 /api/v1 后面，
// 这样将来换成 Tauri、或者让 Agent 直接调接口，都不用改这里。

const { app, BrowserWindow, Menu, shell, dialog } = require("electron");
const { spawn } = require("node:child_process");
const path = require("node:path");
const fs = require("node:fs");
const os = require("node:os");

// 打包后没有控制台，启动过程写到临时文件，出问题好查
const LOG = path.join(os.tmpdir(), "xinge-boot.log");
function boot(msg) {
  try {
    fs.appendFileSync(LOG, new Date().toISOString() + " " + msg + "\n");
  } catch {
    /* 写不了就算了 */
  }
}
boot(`--- 启动 pid=${process.pid} packaged=${app.isPackaged} ---`);

const PORT = Number(process.env.XINGE_PORT || 8722);
const BASE = `http://127.0.0.1:${PORT}`;

// 开发时 ROOT 是 qianfu/；打包后整套运行时在 resources/xinge/
const PACKAGED = app.isPackaged;
const ROOT = PACKAGED
  ? path.join(process.resourcesPath, "xinge")
  : path.resolve(__dirname, "..", "..");

// 打包后自带便携 Python，不依赖系统装没装
const PYTHON = PACKAGED
  ? process.platform === "win32"
    ? path.join(ROOT, "python", "python.exe")
    : path.join(ROOT, "python", "bin", "python3")
  : process.env.XINGE_PYTHON || (process.platform === "win32" ? "python" : "python3");

let backend = null;
let win = null;

function startBackend() {
  const env = { ...process.env, PYTHONIOENCODING: "utf-8" };
  if (PACKAGED) {
    // 嵌入式 Python 靠 ._pth 找包，但仍显式给一份，避免路径意外
    env.PYTHONPATH = [path.join(ROOT, "packages"), ROOT, path.join(ROOT, "astrbot")].join(
      path.delimiter
    );
    env.XINGE_PORTABLE = "1";
  }

  backend = spawn(PYTHON, ["-m", "app.server", "--port", String(PORT)], {
    cwd: ROOT,
    env,
    stdio: ["ignore", "pipe", "pipe"],
  });

  backend.stdout.on("data", (d) => process.stdout.write("[py] " + d));
  backend.stderr.on("data", (d) => process.stderr.write("[py] " + d));
  boot(`拉起后端: ${PYTHON} (cwd=${ROOT})`);

  backend.on("error", (err) => boot(`后端启动失败: ${err.message}`));
  backend.on("exit", (code) => {
    boot(`后端退出 code=${code}`);
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
    backgroundColor: "#eae5d9",
    // 自绘标题栏：标题栏和菜单栏都不要，顶栏直接贴着窗口边
    autoHideMenuBar: true,
    titleBarStyle: "hidden",
    titleBarOverlay: { color: "#172430", symbolColor: "#f6edda", height: 40 },
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
  // 去掉 File/Edit/View/Window/Help 那一条
  Menu.setApplicationMenu(null);
  startBackend();

  const healthy = await waitForHealth();
  boot(`健康检查: ${healthy}`);
  if (!healthy) {
    dialog.showErrorBox(
      "后端启动失败",
      "30 秒内没能连上本地服务。\n\n" +
        (PACKAGED
          ? "运行时目录：" + ROOT
          : "开发模式，请检查 Python 和依赖：\ncd qianfu && python -m app.server")
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
