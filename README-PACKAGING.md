# MinimaxGuard · v0.2.0 桌面打包

## 三种打包方式（按推荐度）

### 1. 🥇 Pake（推荐，已可用）

**适合**：用户已装 Node.js；愿意手动启动 Node 服务

**前置**：
```bash
# 一次性
npm install
npm install -g pake-cli

# ⚠️ 必须：自备 512×512 应用图标
mkdir -p build
# 将图标放到 build/icon.png（PNG，512×512 透明背景）
```

**打包当前平台**：

```bash
./scripts/pake-build.sh

# 或单独平台
npm run package:mac     # macOS .dmg (universal: Intel + Apple Silicon)
npm run package:win     # Windows .exe (NSIS installer)
npm run package:linux   # Linux .deb
```

**产物**（3-5 MB）：
- `MinimaxGuard_0.2.0_universal.dmg` (Mac)
- `MinimaxGuard_0.2.0_x64-setup.exe` (Win)
- `MinimaxGuard_0.2.0_amd64.deb` (Linux)

**使用流程**：
1. 用户安装 `.dmg` / `.exe`
2. **先启动 Node**（监听 `127.0.0.1:5050`，与 Pake `START_URL` 一致）：
   ```bash
   node dist/index.js --desktop
   # 输出：OPEN_URL=http://127.0.0.1:<随机端口>/mobile/<KEY>/
   ```
3. 双击 MinimaxGuard 图标 → 打开 webview → 显示监控面板

> ⚠️ `scripts/pake-build.sh` 当前 `START_URL` 硬编码 `127.0.0.1:5050`（与 Node `--server` 默认一致），但 `--desktop` 模式实际是**随机端口**。如要让 Pake 真正可用，要么改 Node 用固定端口（`PORT=5050 node dist/index.js --desktop`，仍绑 127.0.0.1），要么改 Pake 脚本读取 `OPEN_URL` 日志动态生成 `START_URL`（未实现）。

### 2. 🥈 Tauri（占位，未完工）

**适合**：要求"双击即用"，不依赖任何外部

> ⚠️ **当前状态**：`src-tauri/tauri.conf.json` 已写好配置（`productName: "MinimaxGuard"`, `identifier: "com.auboss.tokenguard"`），但 **没有 Rust 源码**（`src/main.rs` 不存在）、**没有图标**（`icons/*.png` 不存在）、**没有编译脚本**（`package.json` 无 `tauri:build`）、**没有 sidecar binary**（`binaries/minimax-guard-node` 不存在）。`README.md` 与本节都按"已完工"写，但实际**不可用**。

若要启用 Tauri，需自行实现：
```bash
# 安装工具链
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh
cargo install tauri-cli

# 补充缺失文件：
#   src-tauri/src/main.rs       # 启动 Node sidecar + 创建 webview
#   src-tauri/icons/{32x32,128x128,128x128@2x,icon.icns,icon.ico}.png
#   src-tauri/binaries/minimax-guard-node  # Node 二进制（侧车）

# 然后才能 npm run tauri:build  # 此脚本当前不存在，需自行添加到 package.json
```

### 3. 🥉 PWA（不打包）

iPhone Safari → 分享 → "添加到主屏幕" → 桌面图标 → 全屏体验

零编译，零分发，但**不**能离线使用。

## CI/CD 自动发布

> 计划中（`.github/workflows/release.yml` 尚未提交）：
> - push tag → 自动构建三平台
> - 产物作为 GitHub Release Assets 上传

## 文件结构（当前实际）

```
MinimaxGuard/
├── server/              # Node 后端（不变）
├── templates/           # HTML（desktop.html / mobile.html / mobile2.html / settings.html）
├── public/              # 静态资源（当前为空）
├── dist/                # TypeScript 编译输出（.gitignore 排除）
│
├── pake.config.json     # Pake 配置（icon: build/icon.png）
├── scripts/
│   └── pake-build.sh    # Pake 打包脚本（需 build/icon.png）
├── src-tauri/           # ⚠️ Tauri 占位配置（仅有 tauri.conf.json）
│   └── tauri.conf.json
│
├── build/               # ⚠️ 需自建 + 放入 icon.png（512×512）
│   └── icon.png         # Pake 必需
│
└── config.example.json
```

## 常见问题

**Q: 跑 `pake-build.sh` 直接 `exit 1`？**
A: 没找到 `build/icon.png`。Pake 必须有图标。`mkdir -p build` 后放入 512×512 PNG。

**Q: 双击 MinimaxGuard.app 提示"无法连接"？**
A: Node 服务没启动。`--desktop` 模式下 Node 会监听随机端口，请看终端输出的 `OPEN_URL=` 行。考虑改成 `PORT=5050 node dist/index.js --desktop` 固定端口。

**Q: Tauri 怎么编译？**
A: 当前**不能**。需补全 `src-tauri/src/main.rs`、图标、sidecar binary 三件套，再添加 `tauri:build` 脚本。
