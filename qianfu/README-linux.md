# 信鸽 SJTU · Linux

## 三种用法

### 一、AppImage（最省事）

```bash
chmod +x 信鸽SJTU-0.1.0-x64.AppImage
./信鸽SJTU-0.1.0-x64.AppImage
```

不用装，双击就跑。

### 二、deb

```bash
sudo dpkg -i 信鸽SJTU-0.1.0-amd64.deb
sudo apt-get install -f     # 补依赖
信鸽
```

### 三、源码跑

```bash
./start.sh
```

第一次会自动建 `.venv`、装依赖；之后直接起。

## 打 Linux 包

必须在 Linux 上打（electron-builder 不支持从 Windows 交叉打 AppImage）。

```bash
cd qianfu
python scripts/build_linux.py          # 便携运行时
cd app/desktop
npm install
npm run dist:linux                     # AppImage + deb
```

或者推到 GitHub，`build-linux.yml` 会自动在 ubuntu-latest 上跑，产物在 Actions 里下载。

## 和 Windows 版的差别

| 项 | Windows | Linux |
|---|---|---|
| 便携解释器 | python.org embeddable zip | python-build-standalone |
| 解释器路径 | `python/python.exe` | `python/bin/python3` |
| 启动脚本 | `启动.bat` | `start.sh` |
| 安装包 | NSIS `.exe` | AppImage + `.deb` |
| 打包含义 | `npm run dist:win` | `npm run dist:linux` |

## 首次使用

1. 打开，点「机器人」配模型（或跳过，只当信息聚合器用）
2. 点「订阅」→ 扫码登录一次 Canvas（jAccount）
3. 水源、好大学在线、教务会自动跟着登录
4. QQ 机器人可选：填 appid/secret，扫码绑

## 出问题

```bash
# 后端单独跑，看报错
./start.sh

# 打包版的启动日志
tail -f /tmp/xinge-boot.log
```
