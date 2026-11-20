# 开发日志

> 记录「为什么这么做」和「踩过什么坑」，避免下次重新踩一遍。

---

## 2026-11-20 · JAccount 扫码登录（冒烟测试）

### 目标

做一个**不弹浏览器**的统一登录器：在软件里显示二维码，微信扫一下就拿到
Canvas / 物理实验 / CNMOOC 等站点的会话。

### 结论

跑通了。一次扫码覆盖 3 个站点，19 个 cookie 落盘。

```
ws msg: '{"type":"LOGIN","error":0}'
LOGIN received, uuid=53470261-...
expresslogin -> 200 https://oc.sjtu.edu.cn/?login_success=1 cookies=10
```

### 踩到的坑

#### 坑一：jAccount 同时下发两个同名 JSESSIONID

**现象**：手机上显示扫码成功、已确认登录，但程序这边几秒钟就结束，
报「未完成」，cookie 数为 0。

**根因**：登录过程中 jaccount 会设置两个同名的 `JSESSIONID`，靠 `Path` 区分：

| Cookie | Path | 用途 |
|---|---|---|
| `JSESSIONID=...oauth36` | `/oauth2` | OAuth 授权 |
| `JSESSIONID=...jaccount106` | `/jaccount` | 登录页 + WebSocket |

我最初的 `cookie_header()` 只按域名过滤，把两个都拼进了一个 Cookie 头：

```
Cookie: JSESSIONID=xxx.oauth36; JSESSIONID=yyy.jaccount106
```

服务端取第一个，于是 **WebSocket 订阅挂在了 `/oauth2` 的会话上**。
用户扫码后 `LOGIN` 事件发到那个会话，而 `expresslogin` 用的是另一个，
两边对不上。

**修复**：按 path 过滤，WS 只带 `/jaccount` 那个。

```python
cookie = self.session.cookie_header(JA_HOST, path_prefix="/jaccount")
```

**教训**：同域多 cookie 时，`Path` 和 `Domain` 一样重要，
不能只按域名拼 Cookie 头。

---

#### 坑二：httpx 的 `client.cookies = jar` 是拷贝，不是引用

**现象**：修好坑一之后 `LOGIN` 收到了，`expresslogin` 也跟到了 Canvas 回调，
但返回 **422**，cookie 数仍然是 0。

**根因**：

```python
client = httpx.Client(...)
client.cookies = self._jar      # ❌ 这里会 Cookies(cookies) 拷贝一份
```

httpx 的 `cookies` setter 内部是 `self._cookies = Cookies(cookies)`，
赋值后 client 用的是副本。登录页 GET 回来的 `_normandy_session` 落在副本里，
从没进过我们的 jar。

结果：`expresslogin` 带着 code 回调 Canvas 时**没有会话**，
Canvas 无法校验 `state`，直接 422。

**修复**：显式合并回来。

```python
def client(self, **kwargs):
    return httpx.Client(cookies=self._jar, ...)   # 只读地初始化

def merge(self, cookies):                          # 用完同步回 jar
    for c in cookies.jar:
        self._jar.set(c.name, c.value, domain=c.domain, path=c.path)
```

**教训**：第三方库的属性赋值语义要验证，别假设是引用。
凡是"共享状态"的设计，都要写个断言或者单元测试锁住。

---

#### 坑三：图片查看器会锁住二维码文件

**现象**：

```
OSError: [Errno 22] Invalid argument: 'qr-canvas.png'
```

**根因**：上一轮用系统看图打开了 `qr-canvas.png`，Windows 那边还占着句柄，
新进程以 `wb` 打开就失败（这台机器上报的是 EINVAL 而不是 PermissionError，
更有迷惑性）。

**修复**：二维码文件名带上 uuid 前缀，每次都是新文件；
同时 `save()` 把 OSError 包成可读的错误信息。

---

#### 坑四：聊天里的静态图和后台刷新的码不是一张

**现象**：连着几次「扫码后没反应」。

**根因**：二维码约 60 秒过期，后台会定时换新码。但聊天窗口里的图片是静态的，
用户扫的是已经失效的旧码。而且我中间起过多个登录会话，
不同会话的 uuid 不同，扫错了就完全对不上。

**修复**：生成一个本地 HTML 页面，用 `<img>` + `setInterval` 每 2 秒重载图片，
配合后台刷新 png，用户看到的永远是当前有效的码。

**教训**：交互式流程里，**展示端和真实状态必须绑定**，
否则用户看到的东西会骗人。

---

### 方法论

1. **静默失败最难查**。这四个坑全都不抛异常、不崩溃，只是"没登进去"。
   所以加了 `XINGE_DEBUG=1` 写文件日志（`~/.xinge/login.log`），
   每一条 WS 消息、每一次跳转都记下来。有了日志，坑一和坑二都是几分钟定位的。

2. **交互流程要能被观测**。对话里看不到中间状态，必须落到文件。
   日志比 print 靠谱，因为后台任务的 stdout 可能被截断。

3. **一次只改一个变量**。同时改 cookie 过滤和文件命名，出问题就分不清是谁的锅。

### 验证清单

- [x] 不弹浏览器，纯 HTTP + WebSocket 扫码
- [x] 微信扫 jAccount 可用
- [x] 一次扫码覆盖多站点（canvas / pe / cnmooc）
- [x] cookie 跨进程持久化（19 个，4 个域）
- [x] `fetch --source canvas` 不依赖 token
