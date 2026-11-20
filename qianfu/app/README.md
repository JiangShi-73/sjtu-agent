# 信鸽 SJTU · 桌面端

Electron 壳 + Python 后端。**所有能力都在 HTTP 接口后面**，壳只负责开窗口。

## 为什么这么拆

```
┌────────────────────┐        ┌─────────────────────────┐
│  Electron 窗口      │ HTTP   │  FastAPI 后端            │
│  (web/ 静态页面)    │ ─────▶ │  /api/v1/*  +  /ws       │
└────────────────────┘        │  SQLite  ~/.xinge/app.db │
                              └───────────┬─────────────┘
                                          │ import
                                  ┌───────┴────────┐
                                  │  xinge 抓取内核 │
                                  └────────────────┘
```

这么拆的三个好处：

1. **换壳不用改逻辑**——Tauri / 浏览器 / 手机端都只是另一个客户端
2. **Agent 可以直接用**——DSH / Codex 拿到 `http://127.0.0.1:8722` 就能调
3. **能单独调试**——不起 Electron，直接开浏览器也能用

## 目录

```
app/
├── server/            FastAPI 后端
│   ├── main.py          入口、WebSocket、静态挂载
│   ├── db.py            SQLite（items / boards / sources_state / settings）
│   ├── collector.py     抓取调度（App 与 xinge 的唯一接缝）
│   ├── events.py        进程内事件总线
│   ├── repair.py        白名单修复动作
│   └── routers/         health / sources / items
├── web/               前端（原生 JS，无构建步骤）
└── desktop/           Electron 壳
```

## 跑起来

**只跑后端**（推荐先这样调）：

```bash
cd qianfu
export SJTU_API_KEY=xxxxx          # 第二课堂需要
python -m app.server
# 浏览器打开 http://127.0.0.1:8722/static/index.html
```

**跑桌面端**：

```bash
cd qianfu/app/desktop
npm install
npm start
```

Electron 会自动拉起 Python 后端，健康检查通过后再开窗口。

## 桌面快捷方式

`app/desktop/启动信鸽.vbs` 是无控制台的启动器，桌面上已创建快捷方式「信鸽 SJTU」。

它做三件事：

1. **移除** `ELECTRON_RUN_AS_NODE`（注意是移除，不是置空——置空仍会被当成"已设置"）
2. 指定 `XINGE_PYTHON` 为绝对路径，不依赖 PATH
3. 启动 `electron.exe <app 目录>`，用绝对路径而不是 `.`，不依赖当前目录

想重建快捷方式（换图标/换位置）：

```powershell
$ws = New-Object -ComObject WScript.Shell
$sc = $ws.CreateShortcut("$env:USERPROFILE\Desktop\信鸽 SJTU.lnk")
$sc.TargetPath = "C:\Windows\System32\wscript.exe"
$sc.Arguments = '"<项目>\qianfu\app\desktop\启动信鸽.vbs"'
$sc.WorkingDirectory = "<项目>\qianfu\app\desktop"
$sc.IconLocation = "<项目>\qianfu\app\desktop\xinge.ico,0"
$sc.Save()
```

## 配置

API Key 不放在环境变量里（快捷方式启动没有环境变量），统一落在
`~/.xinge/config.json`，由 `/api/v1/settings` 读写：

```json
{
  "sjtu_api_key": "1ae5…02e2",
  "canvas_token": "",
  "fetch_interval_min": 30
}
```

读取优先级：**环境变量 > 配置文件**，方便临时调试。

## 接口一览

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/v1/health` | 存活检查 |
| GET | `/api/v1/diagnose` | 结构化诊断 |
| GET | `/api/v1/logs` | 最近日志 |
| GET | `/api/v1/boards` | 板块 + 未读数 |
| GET | `/api/v1/items` | 条目（可按板块/未读过滤） |
| POST | `/api/v1/items/{id}/read` | 标记已读 |
| GET | `/api/v1/sources` | 信息源状态 |
| POST | `/api/v1/sources/{key}/fetch` | 抓单个源 |
| POST | `/api/v1/sources/fetch-all` | 抓全部 |
| GET | `/api/v1/fixes` | 可用修复动作 |
| POST | `/api/v1/repair` | 执行修复 |
| WS | `/ws` | 事件流 |

`/openapi.json` 自动生成，Agent 可以自己探索。

## 扩展点（刻意留的）

| 想加什么 | 改哪里 |
|---|---|
| 新信息源 | `xinge/sources/` 写个类 + `collector.KNOWN` 补一行 |
| 新板块 | `db.DEFAULT_BOARDS` + `db.SOURCE_BOARD` |
| 新推送通道 | 实现 `Notifier`，接到 `collector` 之后 |
| 新修复动作 | `repair.FIXES` 加一条，必须幂等 |
| 新 API | `server/routers/` 加文件，`main.py` 挂上去 |
| 前端换 React | `web/` 整体替换，后端不用动 |

## 踩过的环境坑

**1. `ELECTRON_RUN_AS_NODE=1`**

如果环境里设了这个变量，Electron 会退化成纯 Node 运行，
`require('electron')` 拿到的是 npm 包路径而不是内置模块，
报 `Cannot read properties of undefined (reading 'whenReady')`。

```bash
unset ELECTRON_RUN_AS_NODE && npm start
```

**2. npm 装 Electron 时报 `unable to verify the first certificate`**

公司/校园网中间人证书导致。用镜像 + 关严格校验：

```bash
ELECTRON_MIRROR=https://npmmirror.com/mirrors/electron/ \
npm_config_strict_ssl=false npm install
```

如果二进制还是没下下来（`node_modules/electron/dist` 只有 LICENSE），
手动下载对应版本的 zip 解压进去，再写 `path.txt`（**不要带换行**）：

```bash
curl -L -o electron.zip https://npmmirror.com/mirrors/electron/33.4.11/electron-v33.4.11-win32-x64.zip
python -c "import zipfile,shutil,pathlib; d=pathlib.Path('node_modules/electron/dist'); shutil.rmtree(d,ignore_errors=True); d.mkdir(parents=True); zipfile.ZipFile('electron.zip').extractall(d)"
printf 'electron.exe' > node_modules/electron/path.txt
```

## 已知限制

- 前端是原生 JS，功能变复杂后建议换 React
- 没有增量调度（现在只能手动点"抓取全部"）
- 插件加载、MCP server、机器人管理都还没做
