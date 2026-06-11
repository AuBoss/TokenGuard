# MinimaxGuard · Minimax 套餐使用量监控

每分钟监测多个 Minimax API key 的 Token Plan 剩余量，提供 **终端实时可视化**、**Web 仪表板** 与 **JSON 追加式历史数据**。

## ✨ 功能

- ⏱️  **每 60 秒** 轮询一次 `https://www.minimaxi.com/v1/token_plan/remains`（间隔可配）
- 🔑  **多 key 并行** 监控：单个配置文件管理多个 Bearer Token
- 💾  **NDJSON 追加存储**：每条记录一行 JSON，便于 grep/jq 二次处理
- 🖥️  **Rich 终端实时面板**：当前状态 + 进度条 + 趋势 sparkline + 最近事件
- 🌐  **Flask Web 仪表板**：浏览器访问 `http://localhost:5000`，支持 Chart.js 折线图
- 🧯  **容错**：网络异常 / JSON 解析失败 / 单 key 失败不会中断其他 key
- 🔌  **离线查看**：`viewer.py` 单独从历史数据生成摘要和最近记录

## 📁 目录结构

```
MinimaxGuard/
├── config.json          # API key 与运行参数
├── monitor.py           # 主监控脚本（终端实时面板）
├── app.py               # Flask Web 服务（独立可选）
├── viewer.py            # 历史数据查看器（独立可选）
├── start.sh             # 一键启动脚本
├── requirements.txt     # Python 依赖
├── data/                # NDJSON 数据（运行时自动创建）
│   └── usage.ndjson
└── README.md
```

## 🚀 快速开始

### 1. 安装依赖

```bash
./start.sh install
# 或手动：
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

### 2. 配置 API Key

编辑 `config.json`：

```json
{
  "api_url": "https://www.minimaxi.com/v1/token_plan/remains",
  "poll_interval_seconds": 60,
  "data_file": "data/usage.ndjson",
  "keys": [
    {
      "alias": "primary",
      "token": "sk-cp-你的token1",
      "tags": ["production"],
      "enabled": true
    },
    {
      "alias": "backup",
      "token": "sk-cp-你的token2",
      "tags": ["staging"],
      "enabled": true
    }
  ]
}
```

- `alias`：在终端/Web 表格中显示的别名
- `token`：Bearer Token（必须以 `sk-cp-` 开头）
- `tags`：可选的分类标签
- `enabled`：设为 `false` 可临时禁用该 key

### 3. 启动监控

#### 方式 A：只跑 Web（推荐 iPhone 用户）

```bash
./start.sh web
```

启动时会**自动启动后台 poller 线程**，每 60s 拉取所有 key 的数据写入 NDJSON，
不再需要单独跑 monitor.py。

终端输出：
```
🔄 后台 poller 已启动: 2 keys, 60s/次
🚀 MinimaxGuard 启动中: http://0.0.0.0:5050
   桌面端:   http://localhost:5050/
   手机端:   http://192.168.31.177:5050/mobile   ← iPhone Safari 打开
```

#### 方式 B：只跑终端监控（需要单独终端）

```bash
./start.sh
# 或
python monitor.py
```

你会看到类似这样的面板（顶部 ASCII banner + 每个 alias 独立 Panel + 折线图 + 事件）：

```
╭─ MINIMAX GUARD ─────────────────────────────────────────╮
│ ● 运行 35s  ⏱ 60s 轮询  🟢 正常 2  ⚪ 异常 0          │
╰────────────────────────────────────────────────────────╯
╭─ 🔑 primary · production · ✅ ONLINE · 🕐 12:34:56 ─╮
│ ⚡ general    🟢  5.0%   2026-06-10 16:00  🟢 20.0%  │
│ ⚡ video      🟢  0.0%   2026-06-10 16:00  🟢  5.0%  │
╰──────────────────────────────────────────────────────╯
```

#### 方式 C：两个都跑

可以同时跑 `./start.sh` 和 `./start.sh web`，它们共享同一个 `data/usage.ndjson`，
不会冲突（都只是追加写）。

### 4. （可选）启动 Web 仪表板

新开一个终端：

```bash
./start.sh web
# 默认监听 0.0.0.0:5050（macOS Monterey+ 的 AirPlay Receiver 占用了 5000 端口）
# 浏览器打开 http://127.0.0.1:5050
```

特性：
- 顶部卡片：每个 key 的当前剩余/总额/已用/使用率进度条
- 趋势图：Chart.js 折线图，可在「全部 / 单个 key」之间切换，可切换「已用 / 剩余 / 总额」三种指标
- 每 30 秒自动刷新，支持手动刷新按钮

### 4.5 📱 iPhone Safari 查看（横屏实时监控）

Web 服务默认绑定 `0.0.0.0:5000`，iPhone 与 Mac 在同一 WiFi 即可访问。

**步骤：**

1. 查 Mac 的局域网 IP（**自动探测，无需手动查**，启动时终端会打印）：
   - 默认输出形如 `🚀 MinimaxGuard 启动中: http://0.0.0.0:5050`
   - 启动时会自动打印本机 LAN IP 和 iPhone 访问地址
   - 如需手动查：`ifconfig | grep "inet " | grep -v 127.0.0.1`

2. 启动 Web 服务：
   ```bash
   ./start.sh web
   ```
   终端会打印：
   ```
   🚀 MinimaxGuard 启动中: http://0.0.0.0:5050
      桌面端:   http://localhost:5050/
      手机端:   http://192.168.31.177:5050/mobile   ← iPhone Safari 打开
   ```

3. iPhone Safari 打开：
   ```
   http://192.168.31.177:5050/mobile
   ```
   （IP 由启动时自动探测，无需手填）

**iPhone 端特性：**
- 纯黑背景 + 等宽字体（`ui-monospace`），横屏一目了然
- 顶部动态 meta：`HH:MM:SS · N keys · M hist · Xs 倒计时`（每秒更新）
- 表格按 (alias × model) 展开，每行：alias、model、5h 百分比+条、 周 百分比+条、 16 字符 sparkline 趋势、 状态、 更新时间
- 颜色编码：5h/周 已用 <60% 绿、60-85% 黄、≥85% 红
- 底部 90px 高的 SVG 折线图（自实现，零依赖），展示每个 (alias, model) 的"5h 已用值"随时间变化
- 60s 自动刷新（不依赖浏览器焦点；时间显示每秒更新）
- 自动随 iPhone Safari 桌面/移动模式自适应

**首次访问会弹出"是否允许 Safari 桌面版网站"等系统提示**

**「添加到主屏幕」**（推荐）：在 iPhone Safari 点击分享按钮 → "添加到主屏幕"，下次从桌面图标打开就是全屏体验。

**常见问题：**
- **5000 端口打不开** → macOS Monterey+ 的 AirPlay Receiver 默认占用 5000 端口。本项目已默认改用 5050 端口，但如果你之前自己改成 5000 就会冲突。改用其他端口（8080 / 5050）即可。
- **AirPlay 占用 5000 端口想关掉** → 系统设置 → 通用 → 隔空播放接收器 → 关闭
- **访问超时** → 防火墙拦截。在 `系统设置 → 网络 → 防火墙` 允许 Python 监听，或临时关闭防火墙测试
- **仍想锁本地** → `./start.sh web --host 127.0.0.1`
- **想换端口** → `./start.sh web --port 8080`

### 5. （可选）查看历史

```bash
# 显示最近 50 条
./start.sh view

# 显示最近 200 条，仅看 primary
./start.sh view --alias primary --last 200

# 显示统计摘要
./start.sh summary

# 自某天起的数据
./start.sh view --from 2026-06-09 --last 500
```

## 🛠️ 直接处理数据文件

数据存为 NDJSON（每行一条 JSON），可用 `jq` 处理：

```bash
# 取出 primary 最近的 5 次 used 值
tail -n 100 data/usage.ndjson \
  | jq -c 'select(.alias=="primary" and .ok)' \
  | tail -n 5 \
  | jq '{ts:.timestamp, used:.used}'

# 统计每个 alias 的失败次数
jq -c 'select(.ok==false) | .alias' data/usage.ndjson | sort | uniq -c

# 把数据喂给 Python pandas
python3 -c "
import pandas as pd
df = pd.read_json('data/usage.ndjson', lines=True)
print(df.groupby('alias')['used'].describe())
"
```

## ⚙️ 进阶配置

| 配置项 | 默认 | 说明 |
|---|---|---|
| `poll_interval_seconds` | 60 | 轮询间隔（秒），官方有速率限制建议 ≥ 30s |
| `request_timeout_seconds` | 15 | 单次 HTTP 请求超时 |
| `data_file` | `data/usage.ndjson` | NDJSON 数据文件路径 |
| `history_limit` | 1440 | 内存中保留的历史条数（24h × 60min） |
| `keys[].alias` | 必填 | 唯一别名 |
| `keys[].token` | 必填 | Bearer Token |
| `keys[].tags` | `[]` | 分类标签 |
| `keys[].enabled` | `true` | 是否参与轮询 |

## 🔐 安全提示

- **不要把 `config.json` 提交到公共仓库**（已加入 `.gitignore` 提示）。建议复制为 `config.local.json` 并加入 git 忽略
- 如果 token 泄露，立即在 Minimax 控制台重置
- 生产部署建议使用 systemd / launchd / Docker 守护进程

### 示例 systemd 单元

```ini
# /etc/systemd/system/minimax-guard.service
[Unit]
Description=Minimax Token Plan Monitor
After=network-online.target

[Service]
Type=simple
WorkingDirectory=/opt/MinimaxGuard
ExecStart=/opt/MinimaxGuard/.venv/bin/python monitor.py
Restart=on-failure
RestartSec=10
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
```

## 🧰 后台运行（macOS launchd）

保存为 `~/Library/LaunchAgents/com.minimax.guard.plist`：

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.minimax.guard</string>
  <key>ProgramArguments</key>
  <array>
    <string>/Users/you/sls/ppp/Auto/AutoGuard/MinimaxGuard/.venv/bin/python</string>
    <string>/Users/you/sls/ppp/Auto/AutoGuard/MinimaxGuard/monitor.py</string>
  </array>
  <key>WorkingDirectory</key>
  <string>/Users/you/sls/ppp/Auto/AutoGuard/MinimaxGuard</string>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>/tmp/minimax-guard.out</string>
  <key>StandardErrorPath</key><string>/tmp/minimax-guard.err</string>
</dict>
</plist>
```

```bash
launchctl load ~/Library/LaunchAgents/com.minimax.guard.plist
```

## 🩺 故障排查

| 现象 | 原因 | 解决 |
|---|---|---|
| 终端面板某 key 持续 FAIL | Token 失效 / 过期 | 在 `config.json` 替换 token |
| 表格里 `剩余 / 总额` 显示 `-` | 接口返回字段名不在候选列表 | 编辑 `monitor.py` 的 `REMAINING_KEYS / TOTAL_KEYS` 加入新字段名 |
| 启动报 `配置文件不存在` | 未创建 `config.json` | 复制 `config.json` 示例并填入 token |
| 终端乱码 | 终端不支持 UTF-8 / TrueColor | 升级到 iTerm2 / Windows Terminal / WezTerm |

## 📜 许可

仅供个人/团队内部使用，请遵守 Minimax 的服务条款与 API 速率限制。
