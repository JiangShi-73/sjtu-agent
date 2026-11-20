# 信鸽 SJTU · 内核（xinge）

> 一个可扩展的「认证 → 抓取 → 推送」内核。
> 当前只做冒烟测试：JAccount 扫码登录 + Canvas 抓取。
> 目标形态：bot 管理 + 信息汇总的综合 App。

## 为什么这样分层

```
core/      纯数据与接口，零依赖（除了标准库）
  models.py      Item / FetchReport —— 上层只认这两个结构
  interfaces.py  Authenticator / Source / Notifier —— 三个 Protocol
  registry.py    装饰器注册，按 key 发现实现

auth/      认证
  session.py     CookieJar 持久化（~/.xinge/session.json）
  sites.py       站点表：SSO 入口 + 登录态判定
  jaccount.py    扫码登录（HTTP + WebSocket，不弹浏览器）

sources/   信息源
  canvas.py      Canvas planner 聚合流
```

新增一个站点 = 往 `sites.SITES` 加一条。
新增一个信息源 = 写个类，打上 `@register_source`。
新增一个推送通道 = 实现 `Notifier`，打上 `@register_notifier`。

## 用法

```bash
cd qianfu

python -m xinge sites                 # 看各站点登录状态
python -m xinge qr --site canvas      # 只要一张二维码
python -m xinge login --site canvas   # 扫码登录（会弹图并等待）
python -m xinge login                 # 依次登录全部站点
python -m xinge fetch --source canvas # 抓 Canvas 的公告/作业
python -m xinge logout                # 清空本地凭据
```

## 扫码登录是怎么实现的

从登录页 JS 逆出来的协议：

1. `GET <站点 SSO 入口>` → 302 到 `jaccount/jalogin`，页面里带 `uuid`
2. 连 `wss://jaccount.sjtu.edu.cn/jaccount/sub/<uuid>`
3. 发 `{"type":"UPDATE_QR_CODE"}`，收到 `{"type":"UPDATE_QR_CODE","payload":{"ts","sig"}}`
4. `GET /jaccount/qrcode?uuid=..&ts=..&sig=..` → PNG
5. 用户用**微信**或 **My SJTU** 扫码
6. WS 收到 `{"type":"LOGIN"}`
7. `GET /jaccount/expresslogin?uuid=..` → 回落到站点，站点写入自己的会话

**一次扫码覆盖所有站点**：jAccount 的会话 cookie 留在同一个 CookieJar 里，
后面几个站点的 OAuth 授权会静默通过。

## 凭据

全部放在 `~/.xinge/session.json`，不进仓库。
可以用环境变量 `XINGE_HOME` 改目录。

Canvas 也支持个人访问令牌，设置 `CANVAS_TOKEN` 即可绕过扫码：

```bash
export CANVAS_TOKEN=xxxxx
python -m xinge fetch --source canvas
```

## 已知问题

- `aihaoke` 登录页需要手动点「统一身份」，且没有可靠的登录态判定，暂时标记为「未检测」
- `educoder` 不走 JAccount，不在统一登录范围内
- 二维码 60 秒左右过期，`wait()` 会自动续期
