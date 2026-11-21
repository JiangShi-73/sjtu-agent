# 微信读书中继

替代 WeWe RSS 依赖的第三方中继 `weread.111965.xyz`（该服务已返回 502）。

WeWe RSS 只调用四个接口，这里全部在本机实现：

| 接口 | 状态 |
|---|---|
| `GET /api/v2/login/platform` | ✅ 真浏览器打开 weread，抠出二维码并解码成扫码链接 |
| `GET /api/v2/login/platform/{id}` | ✅ 长轮询 110 秒，等扫码后返回 token |
| `GET /api/v2/platform/wxs2mp?url=` | ✅ 直接解析公众号文章页（不需要 weread） |
| `GET /api/v2/platform/mps/{id}/articles` | ⏳ 待实现 |

## 跑法

```bash
cd qianfu
python -m relay.server --port 4002
```

然后把 WeWe RSS 的 `PLATFORM_URL` 指过来：

```
PLATFORM_URL="http://127.0.0.1:4002"
```

## 两个坑

**1. `scanUrl` 不是图片地址**

前端是 `<QRCodeSVG value={scanUrl}>`，它要的是二维码里编码的**字符串**。
所以要先把 weread 给的二维码图片解码：

```python
_, binary = cv2.threshold(img, 128, 255, cv2.THRESH_BINARY)
padded = cv2.copyMakeBorder(binary, 40, 40, 40, 40, cv2.BORDER_CONSTANT, value=255)
big = cv2.resize(padded, None, fx=4, fy=4, interpolation=cv2.INTER_NEAREST)
text, _, _ = cv2.QRCodeDetector().detectAndDecode(big)
```

原图只有 171px，不放大识别不出来。

**2. 登录结果接口必须长轮询**

前端超时是 120 秒。如果立刻返回"等待扫码"，它会把非 token 响应当成失败弹红框。
