// electron-builder 配置。
//
// 两个平台用不同的运行时目录，所以用 JS 配置按 XINGE_TARGET 切：
//
//   XINGE_TARGET=win   → build/信鸽SJTU-完整版
//   XINGE_TARGET=linux → build/xinge-linux
//
// 用法：npm run dist:win  /  npm run dist:linux

const path = require("node:path");

const TARGET = process.env.XINGE_TARGET || (process.platform === "win32" ? "win" : "linux");

const RUNTIME = {
  win: "信鸽SJTU-完整版",
  linux: "xinge-linux",
}[TARGET];

// electron-builder 的占位符，拼出来避免转义麻烦
const D = "$";
const WIN_NAME = "信鸽SJTU-安装包-" + D + "{version}." + D + "{ext}";
const LINUX_NAME = "信鸽SJTU-" + D + "{version}-" + D + "{arch}." + D + "{ext}";

const common = {
  appId: "com.sjtu.xinge",
  productName: "信鸽 SJTU",
  copyright: "信鸽 SJTU",
  directories: { output: "../../build/installer", buildResources: "." },
  files: ["main.js", "preload.js", "package.json", "*.ico", "*.png"],
  extraResources: [
    {
      from: path.join("../../build", RUNTIME),
      to: "xinge",
      filter: ["**/*", "!**/*.log", "!**/__pycache__/**", "!**/*.pyc"],
    },
  ],
  asar: true,
};

const win = {
  ...common,
  win: {
    target: [{ target: "nsis", arch: ["x64"] }],
    icon: "xinge.ico",
    artifactName: WIN_NAME,
    // 没证书时别去解 winCodeSign，那里面有 macOS 符号链接，普通用户没权限
    signAndEditExecutable: false,
  },
  nsis: {
    oneClick: false,
    perMachine: false,
    allowToChangeInstallationDirectory: true,
    createDesktopShortcut: true,
    createStartMenuShortcut: true,
    shortcutName: "信鸽 SJTU",
    installerLanguages: ["zh_CN"],
    language: "2052",
  },
};

const linux = {
  ...common,
  linux: {
    target: [
      { target: "AppImage", arch: ["x64"] },
      { target: "deb", arch: ["x64"] },
    ],
    category: "Utility",
    icon: "xinge.png",
    artifactName: LINUX_NAME,
    maintainer: "信鸽 SJTU",
    synopsis: "上海交大校园信息助手",
    description: "抓取 Canvas、活动、学院公告、水源社区，推送到 QQ。",
  },
};

module.exports = TARGET === "win" ? win : linux;
