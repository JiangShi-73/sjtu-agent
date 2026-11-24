# 信鸽 SJTU · GUI 改造对接文档

> 给接手改造界面的模型看的。照着做就能改，不会把功能改坏。

## 一、这是什么

「信鸽 SJTU」是上海交大的校园信息聚合桌面应用。Electron 外壳 + Python 后端，
后端把前端当静态文件发出来（`/static/index.html`），前端是**纯 HTML + CSS + 原生 JS**。

用户能看到的东西：

| 区块 | 作用 |
|---|---|
| 左侧栏 | 板块切换（邮箱 / 全部 / Canvas / 活动 / 实验 / 慕课 / 知途好课 / 学院公告 / 水源社区 / 存档区 / 自定义），底部是功能按钮 |
| 中间主区 | 信息流卡片列表、邮箱列表、整页日历三选一 |
| 右侧栏 | 小日历 + 四个标签页（Canvas / 知途 / 邮件 / 当天）+ 自然语言日程输入框 |
| 登录页 | 整屏欢迎 + jAccount 扫码 |
| 若干弹窗 | 事件编辑、接口管理、问助手、QQ 机器人、订阅、活动详情、诊断 |

## 二、硬约束（不遵守会坏）

### 1. 技术栈不许换

- **没有构建步骤**。浏览器直接加载 `index.html` + `style.css` + `app.js`。
  - 不许引入 React/Vue/打包器/`npm run build`。
  - 不许加 `<script type="module">` 之外的模块化方案。
- **不许用 CDN**。部署环境可能完全断外网（已有实例就是），任何外部字体、图标库、CSS 框架都会导致白屏。
  - 图标要么用 emoji / 内联 SVG，要么用 CSS 画。
  - 字体只用系统字体栈。
- **`app.js` 通过 `document.querySelector('#id')` 拿元素**。删掉或改名任何 `id`，功能立刻失效（且不报错，只是点了没反应）。

### 2. 只改样式和结构，不改 DOM 契约

安全的改法：

- ✅ 改 `style.css`（颜色、间距、圆角、阴影、字体、动画）
- ✅ 在 `index.html` 里**加**外层容器、加装饰元素、调整元素的嵌套层级
- ✅ 调整 `index.html` 里元素的**顺序**（只要还在同一个父容器里能找到）

危险的改法：

- ❌ 删 `id`、改 `id`
- ❌ 删 JS 会去查的 class（见第五节）
- ❌ 把 `hidden` 属性的元素改成 `display:none` 内联样式（JS 用 `.hidden = true/false` 控制显隐）
- ❌ 把 `<button>` 换成 `<div>`（JS 绑 `onclick`，且需要键盘可达）

### 3. 显隐靠 `[hidden]` 属性

CSS 里有一条全局规则：

```css
[hidden] { display: none !important; }
```

JS 全程用 `el.hidden = true/false`。**这条规则必须保留**，否则视图切换全乱。

## 三、文件

```
qianfu/app/web/
├── index.html   337 行  结构
├── style.css    486 行  全部样式
└── app.js      1609 行  逻辑（改造时基本不用动）
```

后端入口：`qianfu/app/server/main.py`，静态目录挂载在 `/static`。

## 四、当前视觉

### 配色

`style.css` 顶部是一组 CSS 变量，**改这里就能整体换肤**：

```css
:root {
  --bg: #f7f8fa;          /* 页面底色 */
  --panel: #ffffff;       /* 面板/卡片底色 */
  --line: #e6e8ec;        /* 分隔线 */
  --text: #1b1f24;        /* 主文字 */
  --muted: #6b7280;       /* 次要文字 */
  --accent: #1f6feb;      /* 主色（蓝） */
  --accent-soft: #e8f0fe; /* 主色浅底 */
  --danger: #c0392b;      /* 危险操作 */
  --radius: 10px;         /* 圆角基准 */
}
```

### 布局

```
body (flex 横排，100vh，overflow:hidden)
├── .sidebar      200px 固定
├── .main         flex:1（内部纵向：.topbar + .view）
└── .rail         288px 固定
```

三栏宽度写死。窗口最小 900×600（Electron 里设的）。

### 排版

- 正文 14px，系统字体栈
- 标题 `.topbar h1` 16px
- 圆角 8–10px，卡片阴影很轻（基本只有边框）

## 五、DOM 契约（改造时一个都不能少）

### 骨架

| id | 元素 | 作用 |
|---|---|---|
| `feed-view` | section | 信息流视图 |
| `mail-view` | section | 邮箱视图 |
| `calendar-view` | section | 整页日历视图 |
| `items` | section | 信息流卡片容器（JS 往里 append） |
| `mail-list` | section | 邮件列表容器 |
| `big-grid` | div | 整页日历网格 |
| `rail-grid` | div | 右侧小日历网格 |
| `boards` | div | 左侧板块按钮容器（JS 动态生成） |

### 左侧栏

| id | 作用 |
|---|---|
| `nav-mail` | 邮箱入口 |
| `mail-badge` | 邮箱未读数角标 |
| `btn-agent` | 打开「问助手」弹窗 |
| `btn-fetch` | 抓取全部 |
| `btn-api` `btn-bot` `btn-feeds` `btn-diag` | 四个功能弹窗入口 |
| `btn-clean` `btn-logout` | 清理 / 退出 jAccount |
| `status` | 底部状态文字 |

### 顶部工具条

| id | 作用 |
|---|---|
| `board-title` | 当前板块名 |
| `only-unread` | 「只看未读」勾选框 |
| `btn-read-all` `btn-select` `btn-clear-read` | 全部已读 / 多选 / 清理已读 |
| `count` | 条数显示 |
| `college-bar` | 学院筛选条（仅学院公告板块显示） |
| `setup-tip` | 配置未完成时的黄色提示条 |

### 右侧栏

| id | 作用 |
|---|---|
| `rail-label` `rail-prev` `rail-next` `rail-expand` | 月份导航 + 展开 |
| `tab-hw` `tab-zhitu` `tab-mail` `tab-day` | 四个标签页按钮 |
| `rail-hw` `rail-zhitu` `rail-mail` `rail-day` | 对应面板 |
| `day-label` `day-list` `day-add` | 当天面板 |
| `chat-log` `chat-input` `chat-send` | 自然语言排日程 |

### 登录页

| id | 作用 |
|---|---|
| `login-page` | 整屏容器（未登录时显示） |
| `login-body` `login-qr-box` | 两个状态：按钮态 / 二维码态 |
| `login-go` `login-cancel` | 开始登录 / 取消 |
| `login-qr-img` `login-qr-tip` | 二维码图 / 状态文字 |

### 弹窗

`event-dlg`（事件编辑）、`api-dlg`（接口管理）、`agent-dlg`（问助手）、
`bot-dlg`（QQ 机器人）、`feeds-dlg`（订阅）、`act-dlg`（活动详情）、`diag`（诊断）、
`login-overlay`（旧扫码浮层，仍保留）。

每个弹窗内部的 `id` 见 `app.js`，**别动**。

### JS 会生成的 class（CSS 里要有对应样式）

```
.board  .badge  .dot  .dots
.item  .item-head  .item-title  .item-summary
.chip  .chip.manual  .chip.derived
.big-cell  .big-day  .big-events  .rail-cell  .rail-day
.day-item  .day-empty  .college-chip  .college-group  .college-head
.course-row  .feed-row  .mail-row  .mail-head  .mail-item
.chat-msg  .diag-section  .pick-bar
.hw-course  .hw-group  .hw-item  .zt-card  .act-row
.login-state  .msg  .name  .ok  .bad  .danger  .mini
```

（这份是 `app.js` 里所有 `className = "..."` 的完整清单，CSS 里都必须有对应样式。）

改这些 class 的**样式**可以，改**名字**不行。

## 六、可以改得多好看

建议方向（按性价比排序）：

1. **换肤**：只动 `:root` 那 9 个变量，就能从「素白」变成任何色调。
   比如深色模式：`--bg:#16181d; --panel:#1e2128; --line:#2c3039; --text:#e6e8ec; --muted:#8b93a1;`
2. **卡片**：`.item` 现在是「白底 + 1px 边框」。可以加圆角、悬浮抬升、左侧色条区分板块。
3. **侧栏**：`.board` 的 active 态现在是浅蓝底。可以换成左侧竖条 + 加粗、或胶囊形。
4. **日历**：`.big-cell` 是纯方格。可以加今日高亮、周末淡色、事件色块圆角。
5. **登录页**：`.login-page` 现在是居中白卡片 + 渐变底。可以加品牌图形、动效。
6. **留白与层级**：整体偏挤。`.items` 的 `padding: 14px 22px 30px` 可以加大，卡片间距从 8px 提到 12–14px。

## 七、怎么验证

```bash
cd qianfu
python -m app.server --port 8730
# 浏览器打开 http://127.0.0.1:8730/static/index.html
```

> 如果本机 `8722` 已经有实例在跑，换个端口（比如 8730）起一份，别去抢端口。
> 前端改完直接刷新页面就生效，不用重启后端——静态文件是每次请求现读的。

验收清单：

- [ ] 左侧每个板块都能点，标题跟着变
- [ ] 卡片能点开事件弹窗
- [ ] 「只看未读」「多选」「全部已读」都还能用
- [ ] 右侧四个标签页能切
- [ ] 日历「今天 / ‹ / ›」能翻月
- [ ] 「问助手」「接口」「机器人」「订阅」「诊断」五个弹窗都能打开关闭
- [ ] 退出登录后回到登录页，登录页按钮能点
- [ ] 窗口拉到 900×600 不塌

## 八、交付方式

只改 `style.css` 和 `index.html`，`app.js` 尽量不动。
如果确实要动 `app.js`，**只加不改**，并说明改了什么、为什么。
