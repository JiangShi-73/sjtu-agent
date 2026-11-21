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

## 界面

```
┌────────┬──────────────────────┬────────────┐
│ 板块   │  信息流 / 整页日历    │  日历栏     │
│        │                      │  + 当日列表 │
│        │                      │  + 对话     │
└────────┴──────────────────────┴────────────┘
```

- 默认是**信息流 + 右侧日历栏**（和 Canvas 一样）
- 点日历栏右上角的 **⤢** → 整页日历，右栏收起
- 整页日历点「返回信息流」回去

## 日历只收三类

不是所有带截止时间的信息都上日历——那样会被活动报名截止塞满。只有：

| 来源 | 类别 |
|---|---|
| Canvas | 作业 |
| 物理实验 | 实验（已选实验） |
| CNMOOC | 作业/测验 |

规则在 `server/db.py` 的 `CALENDAR_RULES`，加一行就能扩。

**日历和右栏共用同一个判断** `db.is_scheduled()`：来源/类别在白名单里、
未提交、未过期，三条都满足才出现。改一处两边同时生效，不会跑偏。

## 右边栏：Canvas 作业

右栏分两个页签：

- **Canvas 作业**（默认）——按课程分组列出**未提交且未过期**的作业
- **当天**——选中那天的日程

数据来自 `GET /api/v1/canvas/homework`，只取未提交的作业。
课程名来自 Canvas 的 `context_name`，不额外请求课程列表。

## 第二课堂活动

数据来自学校开放接口 `/api/open/second-classroom`（X-Api-Key，不走 JAccount）。

能拿到的字段：

| 字段 | 覆盖率 |
|---|---|
| 活动名 / 类别 / 状态 | 39/39 |
| 开始、结束时间 | 39/39 |
| 报名开始、结束时间 | 34/39 |
| 地点 | 39/39 |
| 主办方 / 报名方式 | 39/39 |
| 主讲人及简介 | 8/39 |
| 人数上限 | 24/39 |
| **详情页地址（displayUrl）** | **0/39** |

### 详情页直链怎么来的

开放接口没有 id，但**校园活动平台**（`activity.sjtu.edu.cn`）有。
SSO 之后拿一个 JWT（`IMPSA-Token`，有效期 24 小时，缓存在
`~/.xinge/impsa.json`），带着它请求 `/api/v1/activity/list/home`
就能拿到每条活动的 id。

详情页地址里的 id 是 base64 编码过的：

```
19180 -> "19180"  -> IjE5MTgwIiAg
https://activity.sjtu.edu.cn/activity/detail/IjE5MTgwIiAg?activity_path=/dekt
```

实测 38/38 全部命中。拿不到 token 时自动退回平台首页，不影响列表。

### 活动属性

详情接口 `/api/v1/activity/<id>?detail=1` 的 `custom_properties` 是个 JSON 字符串：

```json
{"是否计入劳动学时":"是","劳动学时":"2","是否计入新时代社会认知实践讲座课":"否"}
```

摊平成 `labor_counted / labor_hours / new_era` 三个字段，卡片上显示成徽章：

- 绿色「劳动学时 2」
- 橙色「新时代讲座」

**平台没有「报酬」字段**——扫过 30 条活动的全部 base_info 字段和 custom_properties，
只有上面三项。

## 信息流操作

- **清理已读**：顶栏按钮，一次删掉所有已读条目
- **删除**：鼠标移到条目上，右侧出现「删除」
- **加入日程**：第二课堂的活动卡片上有这个按钮，一键把活动加成日程

## 物理实验

抓两类：

1. **教学通知**（免登录）
2. **已选实验**（需要 JAccount）——实验名、周次、时间、教室

已选实验会带 `deadline`（实验日期+时间），所以会自动上日历。

## CNMOOC 作业与测验

慕课板块抓两类：**站内消息** + **课程作业与测验**（带截止时间，会上日历）。

### 课程是怎么来的

想自动发现"我选了哪些课"，但课程列表接口 `/portal/ajaxMyCourseIndex.mooc`
对非浏览器请求一律返回 **408**（0.1 秒内直接拒绝，不是超时），
httpx 和 curl 都试过，换了完整浏览器头也没用。所以课程靠**添加一次**：

点侧栏「订阅」→「慕课课程」→ 粘贴课程「测验与作业」页的地址
（形如 `…/examTest/stuExamList/28535.mooc`）。

添加时程序会打开一次该页面，**自动读出课程正式名**，不用手填。
每门课前面有开关，关掉就不再抓。

### 怎么判断课程还在进行

拿不到课程的起止日期，所以用**等价条件**：这门课还有没有未截止的作业。

- 抓取时丢弃已过截止时间的作业
- 抓完再清一遍库里过期的，避免往期作业堆在信息流

课程结束了 → 作业全部过期 → 自然就不再出现在列表里。

## 日程

两个视图：**信息流**（按板块看条目）和**日程**（月历）。

日历上同时画两类东西：

| 类型 | 来源 | 能否编辑 |
|---|---|---|
| 蓝色（manual） | 用户手动或对话创建 | ✅ 点开可改/删 |
| 灰色（derived） | 信息条目里带 `deadline` 的 | ❌ 只读，点击跳原文 |

### 手动建日程

点日历任意一格 → 弹窗填「题目 / 日期 / 从 / 至 / 位置 / 备注」。

### 对话建日程

底部输入框说一句话即可：

```
明天下午3点在图书馆开会
下周三上午10点到11点 组会
11月25日晚上7点 讨论作业
```

解析是**纯规则**实现的（`server/nlp.py`），不依赖大模型，所以：

- 离线可用，不烧 token
- 支持 今天/明天/后天/大后天、周X/下周X/下下周X、X月X日、YYYY-MM-DD
- 支持 上午/下午/晚上/凌晨 + 3点/3点半/15:30、以及 "10点到11点" 这种区间
- 缺日期或缺标题会反问，不猜

没配 AI Key 也能用；将来接上模型只是**增强**，不是前提。

## 浏览器依赖

有几处必须用真浏览器（CNMOOC 课程检测、活动详情截图、活动平台 token）。
换一台电脑不能假设装了 Chromium，所以 `xinge/browser.py` 按顺序找：

1. **Microsoft Edge**（Windows 10/11 系统自带）
2. **Google Chrome**
3. Playwright 自带 chromium（跑过 `playwright install` 才有）
4. 手动扫常见安装路径（Windows 的 Program Files、macOS 的 /Applications、Linux 的 PATH）

都找不到才报错，错误信息里会写清楚试过什么。

打开「诊断」能看到当前用的是哪个浏览器：

```
浏览器
  ✓ 157.0.4341.60
  C:\Program Files\Google\Chrome\Application\chrome.exe
```

接口：`GET /api/v1/system/browser`

## 公众号 / RSS

公众号**没有官方开放接口**，社区通行做法是先转成 RSS 再消费。所以这里拆成两半：

```
公众号  ──(WeWe RSS)──▶  RSS/Atom  ──▶  信鸽（RssSource）
```

信鸽这半已经做好：`xinge/sources/rss.py` 支持 RSS 2.0 和 Atom，
点侧栏「订阅」就能加地址，抓完自动归到「公众号」板块。

### 为什么不在 App 里内置 WeWe RSS

它需要 MySQL（或 Docker），而且登录必须用微信扫码。塞进 Electron 会让
安装包多出几百 MB 和一个数据库进程，得不偿失。**它是独立服务，信鸽只消费它的输出。**

### 部署 WeWe RSS（一次性）

```bash
docker run -d --name wewe-rss \
  -p 4000:4000 \
  -e DATABASE_TYPE=sqlite \
  -e AUTH_CODE=yourpassword \
  -e TZ=Asia/Shanghai \
  -v $(pwd)/wewe-data:/app/data \
  cooderl/wewe-rss-sqlite:latest
```

打开 `http://127.0.0.1:4000`：

1. 账号管理 → 添加账号 → **微信扫码登录微信读书**（注意：不是登录微信本体）
2. 公众号源 → 添加 → 粘贴公众号文章链接
3. 复制该源生成的 Atom 地址，形如 `http://127.0.0.1:4000/feeds/<id>.atom`
4. 回到信鸽 → 订阅 → 粘贴地址 → 板块选「公众号」

### ⚠️ 风险

- **一定用小号**。WeWe RSS 登录的是微信读书，但订阅频率过高会被风控，
  文档里叫「今日小黑屋」，24 小时后解封。
- 不要一次加几十个公众号，分批加。
- 不要用主号——万一被限制，影响的是日常使用的账号。

## QQ 机器人接口

机器人只需要「拉增量 → 推送 → 确认」，不用懂业务。全在 `/api/v1/bot` 下：

| 方法 | 路径 | 用途 |
|---|---|---|
| GET | `/bot/ping` | 探活 |
| GET | `/bot/digest?limit=20&only_unpushed=true` | 还没推送过的条目 |
| POST | `/bot/ack` `{"ids":[1,2]}` | 标记已推送，下次不再给 |
| GET | `/bot/deadlines?days=7` | 未来 N 天的截止事项 |
| GET | `/bot/activities?days=7&with_labor=true` | 未来 N 天的第二课堂活动 |
| GET | `/bot/activities?format=text` | 同上，直接返回可发的文本（带序号） |
| GET | `/bot/activity/{id}` | 单个活动详情 |
| GET | `/bot/activity/{id}/shot.png` | **活动详情页截图**（PNG） |

### 截图接口

`/bot/activity/{id}/shot.png` 用 Playwright 打开真实详情页再截全页图
（1000×1200 视口，2 倍清晰度），结果缓存在 `~/.xinge/shots/` 10 分钟。
第一次约 6 秒，之后秒回。

配合 `format=text` 就能做「问名单 → 报序号 → 发图」：

```
用户：现在第二课堂有什么活动
Bot ：GET /bot/activities?days=14&format=text   →  1. … 2. … 3. …
用户：第三个
Bot ：GET /bot/activity/64/shot.png             →  图片
```

典型循环：

```python
r = httpx.get("http://127.0.0.1:8722/api/v1/bot/digest?limit=20").json()
for it in r["items"]:
    send_qq(f"{it['title']}\n{it['url']}")
httpx.post("http://127.0.0.1:8722/api/v1/bot/ack", json={"ids": [i["id"] for i in r["items"]]})
```

已推送状态记在 `items.pushed_at`，和界面的「已读」互不干扰。

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
| GET | `/api/v1/events` | 日程（含派生截止项） |
| POST | `/api/v1/events` | 新建日程 |
| PATCH | `/api/v1/events/{id}` | 修改日程 |
| DELETE | `/api/v1/events/{id}` | 删除日程 |
| POST | `/api/v1/events/parse` | 只解析不落库（预览） |
| POST | `/api/v1/chat` | 一句话建日程 |
| GET | `/api/v1/feeds` | RSS / 公众号订阅列表 |
| POST | `/api/v1/feeds` | 添加订阅 |
| DELETE | `/api/v1/feeds?url=` | 删除订阅 |
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
