# 桌面 App 架构方案

> 目标：一个电脑上的「机器人管理 + 信息汇总」工作台。
> 状态：方案阶段，尚未开工。

## 一、目标与边界

### 要做的

1. **机器人管理**：启停、配置、看日志、看会话
2. **信息汇总**：多源抓取，按板块展示，未读/已读、过滤、搜索
3. **AI 接入**：填 API Key，选模型，供摘要/分类/问答使用
4. **可扩展**：插件（代码）+ Skill（Markdown）+ MCP（外部工具）
5. **可被 Agent 操控**：DSH / Codex 等能直接读状态、执行动作、修故障

### 不做的

- 不做多用户 / 云端同步（单机单用户）
- 不做移动端（协议预留，但不实现）
- 不内置模型（只接外部 API）
- 不碰登录态以外的高风险操作（如自动选课、自动报名）

---

## 二、参考项目与许可证

| 项目 | 技术栈 | 许可证 | 能借鉴什么 |
|---|---|---|---|
| Dela | Electron + TS | **GPL-3.0** | 模块化单体、微内核注册表、Agent 协议、Skills+MCP 策略 |
| Cherry Studio | Electron + React | **AGPL-3.0** | 多模型接入、Provider 抽象、桌面交互 |
| AnythingLLM | Electron + Node | **MIT** | 文档工作区、向量检索、插件式连接器 |
| Open WebUI | FastAPI + Svelte | **自定义**（含品牌条款） | 只借鉴思路，**不复制代码** |
| Jan | Tauri + Rust | Apache 2.0 | Tauri 桌面壳、本地模型管理 |

### 许可证结论

- 我们的项目是 **AGPL-3.0**（继承 AstrBot）。Dela 的 GPL-3.0 与 AGPL-3.0 **可以组合**，组合后整体按 AGPL-3.0 分发（GPLv3 §13 明确允许）。
- Cherry Studio 同为 AGPL-3.0，直接兼容。
- AnythingLLM 是 MIT，最自由，可以放心抄结构。
- **Open WebUI 是自定义许可，有品牌使用限制**——只能看思路，不能复制代码，也不能用它的 UI 资源。
- 无论抄谁，都要：保留原版权声明、标注修改、随包附 LICENSE。

---

## 三、技术栈

### 后端：Python + FastAPI

理由：抓取、认证、AstrBot 全是 Python。换 Node 等于把 `xinge⟫ 重写一遍。

- **FastAPI**：HTTP + OpenAPI 自动生成（对外接口白送）
- **WebSocket**：事件流（新条目、机器人状态、任务进度）
- **SQLite**：单机够用，零部署
- **APScheduler**：定时抓取

### 前端：React + TypeScript + Vite

- 组件库：Radix + Tailwind（可访问性好，不绑定设计）
- 状态：TanStack Query（服务端状态）+ Zustand（本地 UI 状态）

### 桌面壳：Electron

选 Electron 而不是 Tauri，理由是**少一层不确定性**：

| | Electron | Tauri |
|---|---|---|
| 打包 Python sidecar | 直接 `child_process.spawn` | 需要额外配置 + Rust 侧进程管理 |
| 调试 | Chrome DevTools 直接连 | 受限 |
| 生态/文档 | 最成熟（Dela、Cherry Studio 都用它） | 较新 |
| 体积 | ~150 MB | ~10 MB |

体积是唯一劣势，但对一个本地工具来说可以接受。**先跑通，再优化体积。**

壳的职责被刻意压到最小：拉起 Python、等健康检查、开窗口。业务逻辑一行都不在壳里。

### 进程模型

```
┌──────────────┐   HTTP/WS   ┌─────────────────┐
│  Electron 壳  │ ──────────▶ │  Python 后端     │
│  (WebView)   │ ◀────────── │  FastAPI+SQLite │
└──────────────┘             └─────────────────┘
                                    │
                          ┌─────────┴─────────┐
                          │ AstrBot (可选子进程)│
                          └───────────────────┘
```

后端启动时自动拉起 AstrBot；AstrBot 挂了不影响 App 本身。

---

## 四、分层架构（借鉴 Dela 的模块化单体）

```
app/
├── core/            微内核：注册表、事件总线、协议定义
│   ├── registry.py     插件 / Skill / MCP / 命令 的注册表
│   ├── events.py       事件总线
│   ├── protocol/       对外协议（pydantic 单一来源）
│   └── ports.py        依赖倒置的接口
├── modules/         通用功能（没有交大也成立）
│   ├── bots/           机器人管理
│   ├── feed/           信息流
│   ├── ai/             模型接入
│   ├── plugins/        插件市场/管理
│   └── settings/       设置
├── sjtu/            交大专属
│   ├── jaccount/       扫码登录
│   └── sources/        Canvas / 第二课堂 / 物理实验 / CNMOOC
├── ui/              设计系统（零业务依赖）
└── shell/           Electron 入口
```

**硬规则**（用 CI 检查，Dela 也是这么做的）：

1. `core⟫ 不依赖任何业务模块
2. `ui⟫ 不依赖业务逻辑
3. 模块之间只通过对方暴露的接口调用
4. `core⟫ / `modules⟫ 不能引用 `sjtu⟫

---

## 五、三层扩展机制

这是整个 App 的骨架，必须一开始就定死。

### 1. 插件（代码级）

Python 包，实现标准接口：

```python
class Source(Protocol):
    key: str; name: str; auth: str | None
    def fetch(self) -> FetchReport: ...

class Notifier(Protocol):
    key: str
    def send(self, items: list[Item], target: str) -> bool: ...

class Tool(Protocol):
    key: str
    def run(self, **kwargs) -> dict: ...
```

注册方式：
- 内置：`@register_source⟫ 装饰器（已有）
- 外置：`~/.xinge/plugins/<name>/⟫ 目录 + `plugin.toml⟫，启动时扫描
- 第三方：Python entry_points（pip 安装即可用）

### 2. Skill（Markdown 级）

沿用社区约定 `.agents/skills/<name>/SKILL.md⟫，**和 Dela、Claude Code、Codex 完全通用**。

用户为别的 Agent 写的 Skill，放进这个 App 直接能用；反过来也一样。这是最低成本的生态借力。

### 3. MCP（协议级）

- **作为客户端**：连外部 MCP server（stdio + Streamable HTTP），把它们的工具挂给内置 AI
- **作为服务端**：把 App 的能力暴露出去（见下一节）

MCP 的密钥存系统钥匙串（Windows Credential Manager / macOS Keychain），不落明文。

---

## 六、Agent 控制接口（核心差异化）

这是「出问题用户能自己修」的落点。**三层接口，从粗到细**：

### 第一层：HTTP REST + OpenAPI

```
GET    /api/v1/health              健康总览
GET    /api/v1/diagnose            结构化诊断报告
GET    /api/v1/logs?level=&since=  日志
GET    /api/v1/sources             所有信息源及状态
POST   /api/v1/sources/{key}/fetch 手动触发抓取
GET    /api/v1/items?board=&unread= 信息条目
POST   /api/v1/bots/{id}/restart   重启机器人
POST   /api/v1/repair              执行修复动作
```

FastAPI 自动生成 `/openapi.json⟫，**任何 agent 拿到 URL 就能自己探索接口**。

### 第二层：WebSocket 事件流

```
{"type":"item.new","board":"canvas","item":{...}}
{"type":"source.error","key":"cnmooc","error":"HTTP 500"}
{"type":"bot.status","id":"astrbot","state":"running"}
```

Agent 可以订阅事件，不用轮询。

### 第三层：MCP Server

把 App 暴露成一个 MCP server，agent 直接调工具：

```
xinge.list_sources()        -> 信息源列表
xinge.fetch(source)         -> 抓一次
xinge.list_items(board, n)  -> 看条目
xinge.diagnose()            -> 诊断
xinge.read_logs(level, n)   -> 日志
xinge.apply_fix(fix_id)     -> 执行修复
```

### 修复动作要「白名单化」

不能给 agent 任意 shell。预定义一组**幂等、可回滚**的修复：

| fix_id | 动作 |
|---|---|
| `reauth.<site>⟫ | 重新拉起扫码流程 |
| `restart.source.<key>⟫ | 重启某源的抓取任务 |
| `clear.cache⟫ | 清缓存 |
| `reset.bot.<id>⟫ | 重置机器人配置 |
| `rotate.key.<provider>⟫ | 提示用户换 API Key |

每个动作都有前置检查 + 执行 + 验证三步，执行结果写日志。

---

## 七、信息板块设计

### 板块 = 信息源的逻辑分组

```
板块「课程」    ← Canvas 公告、作业
板块「活动」    ← 第二课堂、交大之声
板块「实验」    ← 物理实验通知
板块「慕课」    ← CNMOOC 站内消息
板块「自定义」  ← 用户加的 RSS / 网页
```

用户可以把任何源拖进任何板块，板块只是视图，不改数据归属。

### 每条信息的统一结构

```python
Item(
  id, source, board,
  title, summary, url,
  category, published, deadline,
  read: bool, starred: bool,
  extra: dict,
)
```

### 三种视图

1. **时间线**：所有板块混排，按发布时间倒序
2. **板块**：分栏，每栏独立滚动
3. **待办**：只看有 `deadline⟫ 且未过期的

---

## 八、AI 接入

- Provider 抽象：OpenAI 兼容 / Anthropic / 本地（Ollama）
- Key 存钥匙串，**不写进配置文件**
- 用途：摘要、分类、问答
- **可关闭**：没配 Key 时所有功能正常，只是没有摘要

---

## 九、数据与存储

```
~/.xinge/
├── session.json        登录态（已有）
├── app.db              SQLite：条目、板块、机器人、设置
├── plugins/            外置插件
├── skills/             Skill
└── logs/               按天滚动
```

条目表加 `hash⟫ 唯一索引做去重，`read_at⟫ 做已读，`pushed_at⟫ 做推送记录——这是接 QQ 之后防刷屏的基础。

---

## 十、里程碑

| 阶段 | 内容 | 依赖 |
|---|---|---|
| M0 | FastAPI 骨架 + 现有四个源接进来 + 简单列表页 | 无（数据侧已就绪） |
| M1 | 板块视图 + 去重存储 + 已读状态 | M0 |
| M2 | Electron 打包成 exe | M1 |
| M3 | 插件加载 + Skill 目录 | M2 |
| M4 | MCP server + 诊断接口 | M3 |
| M5 | 机器人管理（接 AstrBot） | M2 |
| M6 | AI 接入 | M1 |

---

## 十一、合规清单

- [ ] 仓库根放 `LICENSE⟫（AGPL-3.0）
- [ ] 引用的 GPL/AGPL 代码保留原声明
- [ ] 修改过的文件标注 `Modified by ...⟫
- [ ] 不复制 Open WebUI 的代码与 UI 资源
- [ ] 不把 JAccount 凭据、API Key 写进仓库
- [ ] 抓取遵守站点条款，只推标题+摘要+链接
