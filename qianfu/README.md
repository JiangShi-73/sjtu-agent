# 信鸽 SJTU

校园通知抓取 + QQ 推送。**一条命令启动。**

## 启动

```bash
cd qianfu
python main.py
```

和 AstrBot 一样的启动方式。它会依次做：

```
1. 检查 AstrBot 在不在        → 不在就 git clone 到 astrbot/
2. 检查 AstrBot 的虚拟环境     → 不在就建 venv + 装依赖（第一次约 10 分钟）
3. 同步信鸽插件               → integrations/ → astrbot/data/plugins/xinge/
4. 起信鸽后端  :8722
5. 起 AstrBot  :6185
```

`Ctrl+C` 一次全部退出。

**只要信鸽后端**（不要 QQ 机器人）：

```bash
python main.py --no-astrbot
```

## 两个地址

| 服务 | 地址 | 用途 |
|---|---|---|
| 信鸽 | <http://127.0.0.1:8722/static/index.html> | 看通知、作业、学院公告 |
| AstrBot | <http://127.0.0.1:6185> | QQ 机器人面板，配平台和模型 |

## QQ 里能用的命令

插件 `integrations/astrbot_plugin_xinge/` 会同步进 AstrBot，装好后直接用：

| 命令 | 作用 |
|---|---|
| `/信鸽` | 各板块未读数 |
| `/今天` | 未来 7 天要交的东西 |
| `/作业` | Canvas 待交作业 |
| `/慕课` | 慕课作业与测验 |
| `/实验` | 体育实验 |
| `/二课` | 第二课堂活动 |
| `/学院` | 学院列表和条数 |
| `/学院公告 seiee` | 某个学院的公告 |
| `/搜 奖学金` | 全库搜索 |
| `/推送` | 待推送条目 |
| `/确认` | 标记为已推送 |
| `/刷新` | 触发一次全量抓取 |


## 模型配置

AstrBot 用的是 opencode 的 OpenAI 兼容接口，配置写在 `astrbot/data/cmd_config.json`。

迁移后 AstrBot 会把它拆成两层：

~~~json
// provider_sources —— 连哪儿、用什么 key
{"id": "opencode-go_source",
 "provider": "openai",
 "type": "openai_chat_completion",
 "api_base": "https://opencode.ai/inference/go/v1",
 "custom_headers": {"x-opencode-session": "astrbot"}}

// provider —— 用哪个模型
{"id": "opencode-go",
 "model": "deepseek-v4.1-flash",
 "provider_source_id": "opencode-go_source"}
~~~

可用模型（`GET /inference/go/v1/models`）：

~~~
deepseek-v4.1-flash   deepseek-v4-pro   glm-5.3   glm-5.3-flash
grok-4.7              gpt-5.6-luna      gpt-6-luna
claude-haiku-5-5      hy3               hy4-preview
~~~

换模型：面板「模型提供商」改，或直接改 `provider[0].model` 后重启。

> API Key 不写进仓库，存在 `astrbot/data/cmd_config.json`（该目录已 gitignore）。

## 目录

```
qianfu/
├── main.py              统一入口（就是上面那个）
├── app/                 FastAPI 后端 + 前端 + Electron 壳
├── xinge/               抓取内核（各信息源）
├── integrations/
│   └── astrbot_plugin_xinge/   信鸽的 AstrBot 插件
└── astrbot/             AstrBot 检出目录（首次启动自动下载，不进版本库）
```

## 依赖

信鸽后端：

```bash
pip install -r requirements.txt
playwright install chromium
```

AstrBot 的依赖装在 `astrbot/.venv` 里，和信鸽完全隔离——它要装的东西又多又重，
混在一起容易互相顶版本。

## 数据

| 位置 | 内容 |
|---|---|
| `~/.xinge/app.db` | 通知条目、板块、抓取记录 |
| `~/.xinge/config.json` | Canvas token、API Key、订阅列表 |
| `astrbot/data/` | AstrBot 自己的配置和插件 |

删掉 `~/.xinge` 就是全新开始。

## 桌面端

不想要命令行的话：

```bash
cd app/desktop
npm install
npm start
```

或者双击桌面的「信鸽 SJTU」快捷方式。