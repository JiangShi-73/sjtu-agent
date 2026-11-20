// 目前只暴露一个最小的桥。
// 以后要给渲染进程加原生能力（选文件、读钥匙串），都从这里过，
// 不要在渲染进程里直接开 nodeIntegration。
const { contextBridge } = require("electron");

contextBridge.exposeInMainWorld("xinge", {
  version: process.versions.electron,
  platform: process.platform,
});
