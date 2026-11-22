# 部署到学校的 Ubuntu 服务器

以下命令使用示例地址 `192.0.2.10`，请替换为学校云控制台显示的可访问地址。学校实例的可达性取决于路由和访问规则。请先使用校园网或学校提供的 VPN。若要从校外直接访问，需要学校提供可用的公网入口或转发方式。

## 上传发布包

保持交大 VPN 在线。在 **Windows PowerShell** 进入项目目录，构建并打包当前源码。示例 IP 和 SSH 用户名应换成学校控制台提供的信息。下面的命令使用默认 SSH 端口 22。如果控制台显示其他端口，分别在 `scp`、`ssh` 命令中加 `-P 端口`、`-p 端口`。

```powershell
Set-Location 'C:\path\to\PathFinder'
npm.cmd run build
New-Item -ItemType Directory -Force releases | Out-Null
tar -czf '.\releases\pathfinder-release.tar.gz' package.json dist server web fixtures/mvp-demo.json
$pathfinderUser = Read-Host '输入云服务器 SSH 用户名'
scp '.\releases\pathfinder-release.tar.gz' "${pathfinderUser}@192.0.2.10:~/pathfinder-release.tar.gz"
if ($LASTEXITCODE -eq 0) { ssh "${pathfinderUser}@192.0.2.10" }
```

首次连接时，核对主机指纹再接受。需要密码时，在终端的 SSH 密码提示中输入。

发布包包含前端和服务端，以及服务端使用的站点资料。使用 IP 和 HTTP 部署时，网页可以提供访客功能，包括在当前浏览器保存学院选择、查看学院通知和管理待办。站内账号需要后文的 HTTPS 部署步骤。

## 在 Ubuntu 发布网站

以下命令在 **Ubuntu 的 SSH 终端** 执行。用有管理权限的账号运行。若服务器没有安装 Nginx，先执行 `sudo apt update && sudo apt install -y nginx`。

```bash
pathfinder_stage="$(mktemp -d)"
tar -xzf "$HOME/pathfinder-release.tar.gz" -C "$pathfinder_stage"
test -f "$pathfinder_stage/dist/college-bulletins.js" &&
  sudo install -d -m 0755 /var/www/pathfinder &&
  sudo install -m 0644 "$pathfinder_stage"/dist/* /var/www/pathfinder/
```

首次配置站点时，执行下面的命令。已有同名站点时，先检查现有配置，保留原有域名和 HTTPS 设置。

```bash
sudo tee /etc/nginx/sites-available/pathfinder >/dev/null <<'NGINX'
server {
    listen 80;
    server_name 192.0.2.10;
    root /var/www/pathfinder;
    index index.html;

    location / {
        try_files $uri $uri/ =404;
    }
}
NGINX
sudo ln -s /etc/nginx/sites-available/pathfinder /etc/nginx/sites-enabled/pathfinder
```

用下面的命令检查配置并让 Nginx 加载。两个请求都应返回 `200 OK`。

```bash
sudo nginx -t && sudo systemctl enable --now nginx && sudo systemctl reload nginx
curl -fsSI -H 'Host: 192.0.2.10' http://127.0.0.1/
curl -fsSI -H 'Host: 192.0.2.10' http://127.0.0.1/college-bulletins.js
```

从 Windows 浏览器打开 [校内访问地址](http://192.0.2.10/)。进入「个人背景」，选择计算机学院并保存，再回「日程导视」查看标有「本学院」的通知。若服务器本机返回 `200`，但 Windows 浏览器打不开，在云控制台检查安全组入方向 TCP 80 是否对校园网或 VPN 网段开放。若 Ubuntu 启用了防火墙，也检查其 TCP 80 规则。

## 启用站内账号

使用 `sjtupathfinder.cloud` 并继续通过交大 VPN 访问时，可直接按下文「部署 sjtupathfinder.cloud」执行。已有学校 HTTPS 网关时，用下面的通用配置接入。

准备好学校提供的 HTTPS 入口，并安装 Node.js 24 LTS。在上面解包的同一个 Ubuntu 终端执行 `sudo install -d /opt/pathfinder/server`，再用 `sudo install -m 0644 "$pathfinder_stage"/server/*.mjs /opt/pathfinder/server/` 安装账号服务。也请将解包后的 `package.json`、`web/` 和 `fixtures/` 安装到 `/opt/pathfinder/`，目录结构见文末的更新步骤。服务文件位于网站目录之外。

在 `/etc/pathfinder.env` 中设置对外地址，替换示例域名。将文件权限设为仅管理员可读。

```ini
PATHFINDER_ORIGIN=https://pathfinder.example.edu.cn
PATHFINDER_PORT=4173
PATHFINDER_DATA_DIR=/var/lib/pathfinder
```

创建 `/etc/systemd/system/pathfinder.service`。将 Node 路径替换为服务器上 `command -v node` 的实际结果。

```ini
[Unit]
Description=PathFinder account service
After=network.target

[Service]
DynamicUser=yes
StateDirectory=pathfinder
StateDirectoryMode=0700
UMask=0077
EnvironmentFile=/etc/pathfinder.env
ExecStart=/usr/bin/node /opt/pathfinder/server/start.mjs
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

在对外提供 HTTPS 的 Nginx 站点中添加同域代理。如果学校网关终止 TLS，请确保传给服务的 Host 是浏览器使用的对外域名。

```nginx
location /api/ {
    proxy_pass http://127.0.0.1:4173;
    proxy_set_header Host $http_host;
    proxy_set_header X-Real-IP $remote_addr;
    access_log off;
}
```

通过 `sudo systemctl daemon-reload` 和 `sudo systemctl enable --now pathfinder` 启动账号服务，再检查并重新加载 Nginx。通过 HTTPS 打开 `/api/session` 应返回包含 `user` 和 `campus` 的 JSON。SQLite 中保存账号和个人资料，请将其数据目录纳入服务器备份。

## 启用 jAccount

按学校流程申请成员站点和 `basic` 权限，登记完整回调地址 `https://实际域名/api/jaccount/callback`。审核通过后将 `JACCOUNT_CLIENT_ID` 和 `JACCOUNT_CLIENT_SECRET` 设置在服务器的 `/etc/pathfinder.env` 中，重启账号服务。登录页将显示校园登录入口。应用在服务端交换授权码、读取个人标识，再建立本站会话。访问令牌只用于本次登录。

官方文档见 [成员站点申请](https://developer.sjtu.edu.cn/auth/jaccount.html)、[认证接入方法](https://developer.sjtu.edu.cn/auth/oidc.html) 和 [个人身份接口](https://developer.sjtu.edu.cn/api/profile.html)。

## 部署 sjtupathfinder.cloud

下面沿用已部署到 `/var/www/pathfinder` 的网页，目标是从校园网或交大 VPN 打开 `https://sjtupathfinder.cloud` 并登录。若希望普通互联网用户也能访问，需要另有可从公网连接的入口，例如学校提供的公网转发或公网 IP。

### 1. 配置域名解析和安全组

在域名的 DNS 管理页面添加 `A` 记录，主机名填 `@`，地址填 `192.0.2.10`。使用带代理功能的 DNS 服务时，对此记录选择「仅 DNS」。在学校云安全组开放校园网或 VPN 来源到 TCP 443。若 Ubuntu 防火墙已启用，也要放行对应来源的 TCP 443。

### 2. 用 DNS TXT 记录签发证书

在 Ubuntu SSH 终端执行。

```bash
sudo apt update && sudo apt install -y certbot
sudo certbot certonly --manual --preferred-challenges dns \
  --cert-name sjtupathfinder.cloud -d sjtupathfinder.cloud
```

Certbot 会停在 DNS 验证步骤。到域名管理页面新增 `TXT` 记录，主机名为 `_acme-challenge`，值粘贴终端显示的令牌。若管理面板要求完整名称，填 `_acme-challenge.sjtupathfinder.cloud`。等公共 DNS 能查到这一条 TXT 再按 Enter。证书签发后以 Certbot 输出的实际路径为准。

手动 DNS 验证不会自动续期。续期时重新运行相同命令，更新 TXT，成功后运行 `sudo systemctl reload nginx`。[Let's Encrypt DNS 验证](https://letsencrypt.org/docs/challenge-types/#dns-01-challenge) · [Certbot 手动续期](https://eff-certbot.readthedocs.io/en/stable/using.html#manual)

### 3. 安装账号服务

服务器是 Linux ARM64。下面的命令将 Node.js 24 放入 PathFinder 的独立目录。只需要 Node 可执行文件，账号服务没有 npm 依赖。[Node.js 官方 ARM64 下载](https://nodejs.org/en/download/archive/v24.22.3)

```bash
(
  set -e
  node_tmp="$(mktemp -d)"
  cd "$node_tmp"
  node_file=node-v24.22.3-linux-arm64.tar.xz
  curl -fsSLO "https://nodejs.org/dist/v24.22.3/$node_file"
  curl -fsSLO https://nodejs.org/dist/v24.22.3/SHASUMS256.txt
  grep -F "  $node_file" SHASUMS256.txt | sha256sum -c -
  tar -xJf "$node_file"
  sudo install -d -m 0755 /opt/pathfinder/node/bin /opt/pathfinder/server
  sudo install -m 0755 node-v24.22.3-linux-arm64/bin/node /opt/pathfinder/node/bin/node
  tar -xzf "$HOME/pathfinder-release.tar.gz" package.json server web fixtures
  sudo install -d -m 0755 /opt/pathfinder/web /opt/pathfinder/fixtures
  sudo install -m 0644 package.json /opt/pathfinder/
  sudo install -m 0644 web/*.js /opt/pathfinder/web/
  sudo install -m 0644 fixtures/mvp-demo.json /opt/pathfinder/fixtures/
  sudo install -m 0644 server/*.mjs /opt/pathfinder/server/
  /opt/pathfinder/node/bin/node --version
)
```

新建或编辑 `/etc/pathfinder.env`。若文件已有 jAccount 设置，保留已有的设置项。

```bash
sudo touch /etc/pathfinder.env
sudo chmod 0600 /etc/pathfinder.env
sudo nano /etc/pathfinder.env
```

```ini
PATHFINDER_ORIGIN=https://sjtupathfinder.cloud
PATHFINDER_PORT=4173
PATHFINDER_DATA_DIR=/var/lib/pathfinder
```

首次创建 systemd 服务时执行下面的整段命令。已有服务时，按相同内容检查 `/etc/systemd/system/pathfinder.service`，然后执行后面的启动命令。

```bash
sudo tee /etc/systemd/system/pathfinder.service >/dev/null <<'SYSTEMD'
[Unit]
Description=PathFinder account service
After=network.target

[Service]
DynamicUser=yes
StateDirectory=pathfinder
StateDirectoryMode=0700
UMask=0077
EnvironmentFile=/etc/pathfinder.env
ExecStart=/opt/pathfinder/node/bin/node /opt/pathfinder/server/start.mjs
Restart=on-failure

[Install]
WantedBy=multi-user.target
SYSTEMD
```

```bash
sudo systemctl daemon-reload &&
  sudo systemctl enable pathfinder &&
  sudo systemctl restart pathfinder &&
  curl -fsS --retry 5 --retry-connrefused --retry-delay 1 --max-time 3 \
    -H 'Host: sjtupathfinder.cloud' http://127.0.0.1:4173/api/session
```

账号服务初始化 SQLite 后才开始监听端口。`curl` 会在连接被拒绝时短暂重试，成功后应返回 `{"user":null,"campus":false}`。如重试后仍无法连接，执行以下命令查看服务状态、最新日志和端口监听情况。

```bash
sudo systemctl status pathfinder --no-pager -l
sudo journalctl -u pathfinder -n 50 --no-pager
sudo ss -ltnp 'sport = :4173'
```

账号数据库在 `/var/lib/pathfinder`，应纳入服务器备份。

### 4. 启用 Nginx 的 HTTPS 站点

证书签发和账号服务检查通过后，创建域名专用站点。如果 Certbot 输出的路径不同，修改下面两行证书路径。

```bash
sudo tee /etc/nginx/sites-available/sjtupathfinder.cloud >/dev/null <<'NGINX'
server {
    listen 80;
    server_name sjtupathfinder.cloud;
    return 301 https://sjtupathfinder.cloud$request_uri;
}

server {
    listen 443 ssl;
    server_name sjtupathfinder.cloud;
    ssl_certificate /etc/letsencrypt/live/sjtupathfinder.cloud/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/sjtupathfinder.cloud/privkey.pem;

    root /var/www/pathfinder;
    index index.html;

    location / {
        try_files $uri $uri/ =404;
    }

    location /api/ {
        proxy_pass http://127.0.0.1:4173;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        access_log off;
    }
}
NGINX
sudo ln -s /etc/nginx/sites-available/sjtupathfinder.cloud /etc/nginx/sites-enabled/sjtupathfinder.cloud
sudo nginx -t && sudo systemctl reload nginx
curl -fsS --resolve sjtupathfinder.cloud:443:127.0.0.1 https://sjtupathfinder.cloud/api/session
```

以上命令检查本机的证书和 API 转发。接着在 Windows 保持交大 VPN 在线并运行下面的命令，验证从 Windows 到服务器的线路和证书。

```powershell
curl.exe --noproxy '*' --resolve sjtupathfinder.cloud:443:192.0.2.10 https://sjtupathfinder.cloud/api/session
curl.exe --noproxy '*' https://sjtupathfinder.cloud/api/session
```

第一条成功、第二条失败时，重点检查域名解析和本机代理。两条都成功后，通过 [站点首页](https://sjtupathfinder.cloud/) 注册一个试用账号。保存学院和一条待办，退出重登，确认服务器能恢复资料。旧 HTTP 地址的访客资料仍保存在旧地址的浏览器存储中。

站内账号跑通后，再用 `https://sjtupathfinder.cloud/api/jaccount/callback` 申请 jAccount 回调。配置两项学校发放的应用凭据并重启账号服务后，`/api/session` 的 `campus` 应显示为 `true`。

## 在共享服务器上使用独立 HTTPS 端口

本节将 PathFinder 迁移到 `https://sjtupathfinder.cloud:8443/`，沿用已签发的域名证书、静态文件和账号数据库。DNS 记录继续指向 `192.0.2.10`。若还没有安装账号服务，先执行上文「安装账号服务」。

### 检查端口并启用新站点

用 Ubuntu SSH 终端检查 8443 是否已被占用，并核对域名证书的文件路径。

```bash
sudo ss -ltnp 'sport = :8443'
sudo certbot certificates
```

如果 `ss` 显示监听进程，先与维护该服务的成员确认可用端口。以下按 8443 空闲编写。如选用其他端口，配置和验证命令中的 `8443` 要同时替换。在学校云安全组允许校园网或 VPN 来源访问 TCP 8443。若 Ubuntu 防火墙启用，也放行相同来源。

首次创建新的 Nginx 站点时，粘贴下面整段命令。证书文件路径以 Certbot 的输出为准。

```bash
sudo tee /etc/nginx/sites-available/pathfinder-8443 >/dev/null <<'NGINX'
server {
    listen 8443 ssl;
    server_name sjtupathfinder.cloud;
    ssl_certificate /etc/letsencrypt/live/sjtupathfinder.cloud/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/sjtupathfinder.cloud/privkey.pem;

    root /var/www/pathfinder;
    index index.html;
    location / {
        try_files $uri $uri/ =404;
    }

    location /api/ {
        proxy_pass http://127.0.0.1:4173;
        proxy_set_header Host $http_host;
        proxy_set_header X-Real-IP $remote_addr;
        access_log off;
    }
}
NGINX
sudo ln -s /etc/nginx/sites-available/pathfinder-8443 /etc/nginx/sites-enabled/pathfinder-8443
sudo nginx -t && sudo systemctl reload nginx
curl -fsSI --resolve sjtupathfinder.cloud:8443:127.0.0.1 https://sjtupathfinder.cloud:8443/
```

### 切换账号地址并验收

备份 `/etc/pathfinder.env` 后，用 `sudoedit /etc/pathfinder.env` 将 `PATHFINDER_ORIGIN` 改成 `https://sjtupathfinder.cloud:8443`。账号服务用含端口的 `Host` 和 `Origin` 校验请求，所以 Nginx 要用 `$http_host` 转发。4173 仍供 Nginx 在本机调用。

```bash
sudo systemctl restart pathfinder &&
  curl -fs --retry 5 --retry-connrefused --retry-delay 1 --max-time 3 \
    -H 'Host: sjtupathfinder.cloud:8443' http://127.0.0.1:4173/api/session &&
  printf '\n' &&
  curl -siS --max-time 5 --resolve sjtupathfinder.cloud:8443:127.0.0.1 \
    https://sjtupathfinder.cloud:8443/api/session &&
  printf '\n'
```

第一个请求等待账号服务开始监听，第二个请求通过 Nginx 验证 HTTPS。两次都应返回含 `user` 和 `campus` 的 JSON，第二次应同时显示 `200 OK`。若第一个请求失败，查看 `sudo journalctl -u pathfinder -n 30 --no-pager`。若第一个成功而第二个持续返回 `502`，查看 `sudo tail -n 30 /var/log/nginx/error.log`。

如果此前已启用 jAccount，需向交大登记新回调 `https://sjtupathfinder.cloud:8443/api/jaccount/callback`，切换前先确认该端口的回调获准。在交大 VPN 下用浏览器打开 [新站点](https://sjtupathfinder.cloud:8443/)，用现有站内账号登录并核对资料。原地址的浏览器访客数据存储在旧站点地址下。

### 停用 PathFinder 的旧入口

新入口验收后，检查 `/etc/nginx/sites-enabled/`。按照本指南配置时，旧入口使用 `pathfinder` 和 `sjtupathfinder.cloud` 两个符号链接。确认链接对应的配置只包含 PathFinder 的旧站点后，取消启用已有的链接。

```bash
# 只执行对应已存在且已确认归属的链接的命令
if [ -L /etc/nginx/sites-enabled/pathfinder ]; then
  sudo unlink /etc/nginx/sites-enabled/pathfinder
fi
if [ -L /etc/nginx/sites-enabled/sjtupathfinder.cloud ]; then
  sudo unlink /etc/nginx/sites-enabled/sjtupathfinder.cloud
fi
sudo nginx -t && sudo systemctl reload nginx
```

如果旧入口与团队的其他站点在同一个配置文件里，只删去其中 PathFinder 的旧 `server` 块，再测试并重载。需要回滚时，将 `PATHFINDER_ORIGIN` 恢复为之前的地址并重启账号服务，再恢复原站点的链接，取消启用 `pathfinder-8443` 并重载 Nginx。

## 更新已部署的独立端口站点并启用智能体

保持交大 VPN 在线。在 Windows PowerShell 的 PathFinder 项目目录上传新的发布包。

```powershell
$pathfinderUser = Read-Host '输入云服务器 SSH 用户名'
npm.cmd run build
New-Item -ItemType Directory -Force releases | Out-Null
tar -czf '.\releases\pathfinder-assistant.tar.gz' package.json dist server web fixtures/mvp-demo.json
scp '.\releases\pathfinder-assistant.tar.gz' "${pathfinderUser}@192.0.2.10:~/pathfinder-assistant.tar.gz"
```

在 Ubuntu SSH 终端解包和安装。后端要和新网页一起更新，因为对话使用相同版本的机会、公告和日程规则。

```bash
(
  set -e
  stage="$(mktemp -d)"
  tar -xzf "$HOME/pathfinder-assistant.tar.gz" -C "$stage"
  sudo install -d -m 0755 /opt/pathfinder/web /opt/pathfinder/fixtures
  sudo install -m 0644 "$stage"/package.json /opt/pathfinder/
  sudo install -m 0644 "$stage"/web/*.js /opt/pathfinder/web/
  sudo install -m 0644 "$stage"/fixtures/mvp-demo.json /opt/pathfinder/fixtures/
  sudo install -m 0644 "$stage"/server/*.mjs /opt/pathfinder/server/
  sudo install -m 0644 "$stage"/dist/* /var/www/pathfinder/
)
```

执行 `sudoedit /etc/pathfinder.env`，新增 `PATHFINDER_AI_API_KEY` 并填入在交大平台重新签发的密钥。保留当前的 `PATHFINDER_ORIGIN=https://sjtupathfinder.cloud:8443`、数据库目录和已有的登录设置。交大 API 地址已在后端配置。保存后执行下列命令。

```bash
sudo systemctl restart pathfinder &&
  curl -fs --retry 5 --retry-connrefused --retry-delay 1 --max-time 3 \
    -H 'Host: sjtupathfinder.cloud:8443' http://127.0.0.1:4173/api/session
```

连接交大 VPN，刷新 [PathFinder](https://sjtupathfinder.cloud:8443/) 并登录。在「日程导视」询问「这周有哪些截止事项」，再试「请给我新建一条明天截止的待办，标题是整理简历」。点击保存并确认事项出现在「截止事项」。如果页面显示模型连接失败，请检查 Ubuntu 是否可访问交大模型网关。[交大模型 API 文档](https://apps.hpc.sjtu.edu.cn/posts/claw-guide-sjtu-api)
