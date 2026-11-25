# 第三方组件与许可声明

本项目（信鸽 SJTU）整体以 **AGPL-3.0-or-later** 发布，详见仓库根目录 `LICENSE`。

之所以采用 AGPL-3.0，是因为本项目基于 **AstrBot**（AGPL-3.0）构建 QQ 机器人部分，
按 AGPL-3.0 的传染性要求，包含 AstrBot 的整件作品需以同一许可发布。

---

## 一、主要第三方组件

| 组件 | 用途 | 许可证 | 版权 |
|---|---|---|---|
| **AstrBot** | QQ 官方机器人框架（插件宿主） | **AGPL-3.0** | Copyright (c) Soulter / AstrBotDevs |
| Electron / Chromium | 桌面应用外壳 | MIT / BSD-3-Clause 等 | Copyright (c) Electron contributors, Google Inc. |
| FastAPI | 后端 Web 框架 | MIT | Copyright (c) Sebastián Ramírez |
| Starlette | ASGI 框架（FastAPI 依赖） | BSD-3-Clause | Copyright (c) Encode OSS Ltd. |
| Uvicorn | ASGI 服务器 | BSD-3-Clause | Copyright (c) Encode OSS Ltd. |
| HTTPX | HTTP 客户端 | BSD-3-Clause | Copyright (c) Encode OSS Ltd. |
| Pydantic | 数据校验 | MIT | Copyright (c) Pydantic Services Inc. |
| websockets | WebSocket 协议实现 | BSD-3-Clause | Copyright (c) Aymeric Augustin and contributors |
| websocket-client | WebSocket 客户端 | Apache-2.0 | Copyright (c) liris and contributors |
| Playwright | 无头浏览器自动化 | Apache-2.0 | Copyright (c) Microsoft Corporation |
| Pillow | 图片渲染 | HPND / MIT-CMU 系 | Copyright (c) Secret Labs AB, Fredrik Lundh |
| electron-builder | 打包工具（仅开发期） | MIT | Copyright (c) Loopline Systems |

> 说明：Playwright 仅驱动**用户本机已安装**的 Edge/Chrome，不在分发物中内置浏览器内核，
> 因此不涉及 Chromium 的再分发义务。

## 二、AstrBot 相关声明

- AstrBot 的完整源码随便携版分发包提供（`astrbot/` 目录），并保留其原始
  `LICENSE`、`EULA.md`、`FIRST_NOTICE.md` 等文件，未做修改。
- 本项目的 QQ 插件 `qianfu/integrations/astrbot_plugin_xinge/main.py`
  通过 `from astrbot.api import ...` 与 AstrBot 链接，属于 AGPL-3.0 意义上的衍生作品，
  因此以 AGPL-3.0-or-later 发布。
- 我们将 AstrBot 的匿名遥测在启动时通过 `ASTRBOT_DISABLE_METRICS=1` 关闭。

## 三、参考与致谢

本项目在设计与实现思路上参考了以下开源项目/产品，**未直接复制其源代码**：

| 项目 | 许可证 | 参考点 |
|---|---|---|
| Dela | GPL-3.0 | 校园信息聚合的总体思路、IMAP 读取邮箱的做法 |
| Cherry Studio | AGPL-3.0 | 桌面端多模型管理的交互参考 |
| AnythingLLM | MIT | 本地知识库/文档处理的工程组织方式 |
| Open WebUI | 自定义（BSD-3-Clause 衍生） | 本地模型接入的配置界面参考 |

如后续发现在实现中直接引用了上述项目的代码，将按对应许可证要求补充声明并保持兼容。

## 四、字体与素材

- 界面使用系统字体栈（无内置字体文件）。
- 应用图标为本项目自行绘制。

## 五、声明

信鸽 SJTU 是**上海交通大学"交我创"智能体创新大赛的参赛作品，非上海交通大学官方产品**。
