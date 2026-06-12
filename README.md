# MinimaxGuard · Minimax 套餐使用量监控

每分钟监测多个 Minimax API key 的 Token Plan 剩余量，提供三种部署形态：

- **🌐 Web 仪表板** —— iPhone / 桌面浏览器友好的深色 SPA，**WebSocket 秒级实时推送**、24h 折线图、alias 切换器、Settings 配置页
- **🖥️ 桌面应用** —— 通过 [Pake](https://github.com/tw93/Pake) 把 Web 仪表板打包为 Mac/Win/Linux 原生应用（~3-5 MB）
- **🔒 反向代理** —— nginx + access key 鉴权的安全部署方案

后端：**Node.js 18+ · TypeScript · Express · ws**；数据：**NDJSON 追加式历史**；前端：**纯 HTML + Chart.js，零构建**。

> **v0.2.0** —— 新增桌面打包（Pake）、`--server` / `--desktop` 双模式、`/api/config*` 运行时配置 API、Settings 配置页。`src-tauri/` 仅为占位配置，**尚未完工**。

## 📸 预览

![MinimaxGuard 仪表板](docs/screenshot.png)

> 深色仪表板：实时 5h / 周用量、5h 倒计时、剩余额度、4 张 key 卡片自适应网格、24h 折线趋势图。桌面端 `desktop.html` / 移动端 `mobile.html` / 配置页 `settings.html` 三视图。

## ✨ 功能

| 维度 | 详情 |
|---|---|
| **轮询** | 每 60s 调用一次 `https://www.minimaxi.com/v1/token_plan/remains`（可在 `config.json` 调） |
| **多 key** | 单配置文件管理多个 Bearer Token，独立 alias、tags、enabled 标志 |
| **存储** | NDJSON 追加（每行一条 JSON），便于 `grep` / `jq` 处理；内存上限 50000 条 |
| **Web 仪表板** | 桌面端 `desktop.html` + 移动端 `mobile.html` + 配置页 `settings.html`；dark theme；Chart.js 折线图 |
| **WebSocket 推送** | Node.js `ws` 服务，poller emit `record` → 广播 `usage_update`，首屏 < 1s（`request_poll`） |
| **alias 切换器** | iPhone 顶部下拉单选 alias / 全部，`localStorage` 持久化 |
| **运行时配置** | `/api/config*` 增删改 alias/token，**无需重启** poller |
| **5h 倒计时** | 移动端实时显示距下个 5h 窗口重置的剩余时间 |
| **访问 key** | 32 字符 URL-safe 随机串，URL 段或 `X-Access-Key` header 鉴权 |
| **gzip 压缩** | `compression` 中间件，history 接口 ~720 kB → ~430 B（view=mobile） |
| **双启动模式** | `--server` 模式监听 `HOST:PORT`（生产）；`--desktop` 模式监听 `127.0.0.1:<随机>`（不暴露公网） |
| **桌面应用** | 通过 Pake 打包 Mac (.dmg) / Win (.exe) / Linux (.deb)（详见 [README-PACKAGING.md](./README-PACKAGING.md)） |
| **容错** | 单 key 失败不影响其他 key；JSON 解析失败跳过该行；timeout 默认 15s |

## 📁 目录结构

```
MinimaxGuard/
├── server/                     # Node.js + TypeScript 后端
│   ├── index.ts                # Express 主程序（API + 静态 + WS + config + 模式切换）
│   ├── poller.ts               # 60s 后台轮询（EventEmitter，try/finally 防死锁）
│   ├── ws.ts                   # WebSocket 服务（8 种 server 消息 / 5 种 client 消息）
│   ├── store.ts                # NDJSON 数据访问层
│   ├── config.ts               # 配置管理（运行时增删 alias，热更新）
│   ├── utils.ts                # 工具：access key 生成、API 字段映射
│   └── types.ts                # TypeScript 类型
├── templates/                  # HTML 模板（运行时注入 <KEY> / <BASE_URL>）
│   ├── desktop.html            # 桌面端 SPA（响应式 grid）
│   ├── mobile.html             # iPhone 端 SPA（3 列 + 趋势图，<720px 单列）
│   ├── mobile2.html            # 与 mobile.html 完全相同，仅 URL 不同以绕过 iPhone HTML 缓存
│   └── settings.html           # 配置管理页（增删 alias / token / tags / enabled）
├── public/                     # 静态资源（当前为空）
├── dist/                       # TypeScript 编译输出（被 .gitignore 排除）
│
├── config.json                 # API tokens（被 .gitignore 排除，**不入库**）
├── config.example.json         # 示例配置
├── data/                       # NDJSON 历史 + access_key（被 .gitignore 排除）
│   ├── usage.ndjson
│   └── access_key              # 32 字符 URL-safe key（chmod 600）
│
├── package.json                # Node 依赖 + 打包脚本
├── package-lock.json
├── tsconfig.json               # strict: true, ES2022
├── pake.config.json            # Pake 桌面打包配置
├── scripts/
│   └── pake-build.sh           # Mac/Win/Linux 桌面打包脚本（需 build/icon.png）
├── src-tauri/                  # ⚠️ Tauri 占位配置（暂无 Rust 源码，未完工）
│   └── tauri.conf.json
├── nginx-minimax.conf          # nginx 反代配置片段（主用，路径 /mobile/ / / /api/ /ws /minimax/）
├── nginx-key-prefix.conf       # 变体：通用 key 路径代理（if 方式）
├── nginx-key-prefix2.conf      # 变体：通用 key 路径代理（正则 location）
├── nginx-mobile2.conf          # 变体：/mobile2/ 路径专用
├── README-PACKAGING.md         # 详细桌面打包文档
├── LICENSE                     # MIT
└── README.md
```

## 🖥️ 桌面应用（v0.2.0 新增）

支持 Mac/Win/Linux 三平台桌面应用，详见 [README-PACKAGING.md](./README-PACKAGING.md)。当前进度：

| 方式 | 状态 | 产物大小 | 说明 |
|---|---|---|---|
| **Pake** | ✅ 可用 | 3-5 MB | 把 Node 服务 + Webview 打包成原生应用；**需自备 `build/icon.png`（512×512）** |
| **Tauri** | ⚠️ 占位 | — | `src-tauri/tauri.conf.json` 已写好，但**无 Rust 源码、无图标、未编译**，需自行实现 `src/main.rs` |
| **PWA** | ✅ 可用 | 0 | iPhone Safari → 分享 → 添加到主屏幕，全屏体验 |

**Pake 快速打包**：

```bash
# 前置：需 npm install -g pake-cli；需自备 512x512 的 build/icon.png
./scripts/pake-build.sh

# 或单独平台
npm run package:mac     # macOS .dmg (universal)
npm run package:win     # Windows .exe (NSIS)
npm run package:linux   # Linux .deb
```

**使用流程**：用户先在终端运行 `node dist/index.js --desktop`（Node 监听 `127.0.0.1:<随机端口>`，**不**暴露公网），再双击 MinimaxGuard 图标，webview 打开 `http://127.0.0.1:<端口>/mobile/<KEY>/`。

> ⚠️ `scripts/pake-build.sh` 当前硬编码 `START_URL` 端口为 `5050`（默认），与 Node 默认一致；如改用 `PORT` 环境变量启动 Node，记得同步 `pake-build.sh` 的 `START_URL`。

## 🚀 快速开始

### 1. 安装依赖

需要 Node.js ≥ 18（推荐 20 LTS）。

```bash
# Ubuntu/Debian
curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
sudo apt-get install -y nodejs

# 或用 binary tarball
curl -fsSL https://nodejs.org/dist/v20.20.2/node-v20.20.2-linux-x64.tar.xz -o /tmp/node.tar.xz
sudo tar -xJf /tmp/node.tar.xz -C /opt
sudo ln -sf /opt/node-v20.20.2-linux-x64/bin/{node,npm,npx} /usr/local/bin/

# 安装项目依赖
cd /path/to/MinimaxGuard
npm install
```

### 2. 配置 API Key

```bash
cp config.example.json config.json
nano config.json
```

```json
{
  "api_url": "https://www.minimaxi.com/v1/token_plan/remains",
  "poll_interval_seconds": 60,
  "request_timeout_seconds": 15,
  "data_file": "data/usage.ndjson",
  "history_limit": 50000,
  "keys": [
    { "alias": "primary", "token": "sk-cp-你的token", "tags": ["production"], "enabled": true },
    { "alias": "backup",  "token": "sk-cp-你的token", "tags": ["staging"],    "enabled": true }
  ]
}
```

### 3. 编译 + 启动

```bash
# 开发模式（tsx watch + 类型检查）
npm run dev

# 生产模式
npm run build        # tsc → dist/
npm start            # node dist/index.js（默认 --server 模式）

# 显式指定启动模式
npm run start:server   # node dist/index.js --server
npm run start:desktop  # node dist/index.js --desktop（127.0.0.1 随机端口）
npm run start:prod     # NODE_ENV=production
```

启动输出：

```
[init] access key loaded (32 chars)
[init] config: 2 keys, mode=server
[init] loaded 2304 historical records from data/usage.ndjson
[poller] started, interval=60s, keys=2
[server] listening on http://127.0.0.1:5050 (mode=server)
[server] mobile:   http://127.0.0.1:5050/mobile/<KEY>/
[server] ws:       ws://127.0.0.1:5050/ws?key=<KEY>
```

### 4. nginx 反向代理

`nginx-minimax.conf` 包含完整配置块（`/mobile/`, `/mobile2/`, `/`, `/minimax/`, `/api/`, `/ws`）。

关键点：
- WebSocket 必须有 `Upgrade` + `Connection: upgrade` 头
- `/mobile/`, `/mobile2/`, `/minimax/`, `/api/`, `/ws` 都代理到 5050
- `Cache-Control: no-cache, no-store, must-revalidate` 给所有 API
- `/<KEY>/(.*)` 兼容 iPhone 旧 JS 缓存（fetch URL 含 key 段）—— 变体在 `nginx-key-prefix*.conf`

详见仓库内 4 个 `nginx-*.conf` 文件。

## 📡 API 端点

| 端点 | 方法 | 说明 |
|---|---|---|
| `/api/health` | GET | 健康检查（无需 key）|
| `/api/diag` | GET | poller 状态 + 记录数 + 上次轮询时间 |
| `/api/keys` | GET | 全部 key 的最新状态（精简） |
| `/api/history?limit=2000&hours=24&alias=primary&view=full` | GET | 历史时序数据（`limit` ≤ 20000；`hours` 0=不按时间过滤；`view=mobile` 仅返回 5h 窗口的 rp/u） |
| `/api/config` | GET | 完整配置（token 自动脱敏：前 6 位 + `...` + 后 4 位） |
| `/api/config/keys` | POST | 新增 alias `{ alias, token, tags? }`；立即生效 |
| `/api/config/keys/:alias` | PUT | 修改 alias 配置（token/tags/enabled） |
| `/api/config/keys/:alias` | DELETE | 删除 alias |
| `/settings` | GET | Settings 配置管理页（HTML） |
| `/` | GET | `desktop.html` 渲染（key 注入） |
| `/mobile` | GET | `mobile.html` 渲染（key 注入） |
| `/mobile2` | GET | `mobile2.html` 渲染（与 mobile 路径不同，强制 iPhone 重下） |
| `/ws?key=<KEY>` | WS | WebSocket 实时推送 |

### WebSocket 协议

**握手**：
- URL 参数：`ws://host/ws?key=<KEY>`（带 access key，pre-authed）
- 或连接后首条消息：`{ "type": "auth", "key": "<KEY>" }`（无 `?key=` 时必须）

**服务端 → 客户端**：

| type | 触发 | 负载 |
|---|---|---|
| `hello` | 连接建立 | `{ auth_required: <bool> }` |
| `auth_ok` | `{type:'auth'}` 成功 | `{}` |
| `usage_update` | poller 产生新记录 | `{ record: { alias, ts, ok, error?, models? } }` |
| `subscribed` | 收到 `subscribe` | `{ aliases: 'all' \| [...] }` |
| `unsubscribed` | 收到 `unsubscribe` | `{}` |
| `poll_triggered` | 收到 `request_poll` | `{}` |
| `pong` | 收到 `ping` | `{ ts: <number> }` |
| `error` | 鉴权失败 / 非法消息 | `{ message: '...' }` |

**客户端 → 服务端**：

```json
{ "type": "auth", "key": "<KEY>" }              // 鉴权（URL 未带 key 时必发）
{ "type": "subscribe", "aliases": "all" }       // 订阅全部 / { "aliases": ["primary", "Team"] }
{ "type": "unsubscribe" }                        // 重置为 'all'
{ "type": "ping" }                               // 心跳
{ "type": "request_poll" }                       // 立即触发一次轮询并广播
```

**前端数据流（mobile）**：
1. 启动时 `refresh({withHistory: true})` 拉 24h 历史画图
2. WebSocket 连接后发 `request_poll`，server 立即推一次首屏
3. 每 60s 再 fetch 一次 history（画图新数据点）
4. 真实轮询（每 60s）→ WS push → 增量更新 cards（**不再 fetch**）

## ⚙️ 进阶配置

`config.json` 字段：

| 字段 | 默认 | 说明 |
|---|---|---|
| `api_url` | `https://www.minimaxi.com/v1/token_plan/remains` | Minimax 套餐查询端点 |
| `poll_interval_seconds` | 60 | 轮询间隔（秒），官方建议 ≥ 30s |
| `request_timeout_seconds` | 15 | 单次 HTTP 请求超时 |
| `data_file` | `data/usage.ndjson` | NDJSON 数据文件路径 |
| `history_limit` | 50000 | 内存中保留的历史条数 |
| `keys[].alias` | 必填 | 唯一别名 |
| `keys[].token` | 必填 | Bearer Token（`sk-cp-...`） |
| `keys[].tags` | `[]` | 分类标签 |
| `keys[].enabled` | `true` | 是否参与轮询 |

**环境变量**（覆盖默认行为）：

| 变量 | 默认 | 说明 |
|---|---|---|
| `PORT` | 5050 | HTTP/WS 监听端口（`--desktop` 模式下被忽略，使用随机端口） |
| `HOST` | 127.0.0.1 | 绑定地址（生产建议 127.0.0.1 + nginx） |
| `CONFIG_PATH` | `config.json` | 配置文件路径 |
| `KEY_FILE` | `data/access_key` | access key 文件路径 |
| `DATA_FILE` | `data/usage.ndjson` | NDJSON 数据路径 |

## 🔐 安全提示

- **不要把 `config.json` 提交到公共仓库**（已加入 `.gitignore`）
- access_key 在 `data/access_key`（chmod 600），**不会**被 .gitignore 误排除
- WebSocket 鉴权：URL `?key=` 或首条消息 `{type:'auth'}` 二选一
- nginx 反代务必配置 `Cache-Control: no-cache` 给所有 API 响应
- 如果 token 泄露，立即在 Minimax 控制台重置，并删 `data/access_key` 重启服务
- `--desktop` 模式仅监听 `127.0.0.1` 随机端口，**不**暴露公网，但仍需本机访问控制

## 🛠 生产部署（systemd）

`/etc/systemd/system/minimax-guard-node.service`：

```ini
[Unit]
Description=MinimaxGuard (Node.js + WebSocket)
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/opt/MinimaxGuard
Environment=PORT=5050
Environment=HOST=127.0.0.1
ExecStart=/usr/local/bin/node /opt/MinimaxGuard/dist/index.js --server
Restart=always
RestartSec=5
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=full
ReadWritePaths=/opt/MinimaxGuard /opt/MinimaxGuard/data
UMask=0077
StandardOutput=journal
StandardError=journal
SyslogIdentifier=minimax-guard-node

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now minimax-guard-node
journalctl -u minimax-guard-node -f
```

## 🐛 故障排查

| 现象 | 原因 | 解决 |
|---|---|---|
| `ECONNREFUSED 127.0.0.1:5050` | Node 服务未启动 | `systemctl status minimax-guard-node` |
| iPhone 一直显示旧布局 | Safari HTML 缓存 | 改用新 URL `/mobile2/<KEY>/` 强制重下 |
| WebSocket 连不上 | nginx 没加 `Upgrade` 头 | 确认 `nginx-minimax.conf` 包含 `proxy_set_header Upgrade $http_upgrade` |
| `/<KEY>/api/` 404 | nginx `location /` 兜底 | 确认 nginx 已加载 `location ~ "^/([A-Za-z0-9_-]{16,128})(/.*)$"` 块（变体在 `nginx-key-prefix2.conf`） |
| 启动报 `config.json: ENOENT` | 未创建 config | `cp config.example.json config.json` 并填 token |
| API 返回 `Not Found` | URL 没带 key | 加 `?key=...` 或 header `X-Access-Key: ...` |
| 桌面模式找不到端口 | Node 使用随机端口 | 启动时观察 `OPEN_URL=http://127.0.0.1:<port>/mobile/<KEY>/` 日志 |
| Pake 打包 `exit 1` | 缺 `build/icon.png` | 自备 512×512 PNG 放在 `build/icon.png` |

## 🔄 v0.1.0 → v0.2.0 迁移

| 项 | v0.1.0 | v0.2.0 |
|---|---|---|
| 桌面应用 | — | ✅ Pake 打包（Mac/Win/Linux），Tauri 占位配置 |
| 启动模式 | 单模式 | `--server` / `--desktop` 双模式（端口策略不同） |
| 配置 API | 改 `config.json` 需重启 | `/api/config/keys*` 运行时增删改，poller 热重载 |
| Settings 页 | — | ✅ `templates/settings.html` Web UI 管理 alias |
| 启动脚本 | `npm start` | 新增 `start:server` / `start:desktop` / `start:prod` |
| 默认端口 | 5050 | 5050（不变） |
| 数据兼容 | NDJSON 不变 | **完全兼容**，可直接复用 `data/usage.ndjson` |

**升级步骤**：
```bash
git pull
npm install
npm run build
# 旧 systemd 单元无需改（PORT/HOST 仍是 5050/127.0.0.1）
sudo systemctl restart minimax-guard-node
```

## 📜 版本历史

- **v0.2.0** —— Node.js + TypeScript + Pake 桌面打包 + Settings 配置页 + `/api/config*` 运行时配置
- **v0.1.0** —— Node.js + TypeScript + WebSocket 重构（从 Python/Flask 迁移）
- **v0.0.0** —— Python/Flask 基线

> 详细迁移见上文「v0.1.0 → v0.2.0 迁移」与 git tag `v0.1.0`。

## 📜 许可

[MIT License](./LICENSE) —— 仅供个人/团队内部使用，请遵守 Minimax 的服务条款与 API 速率限制。
