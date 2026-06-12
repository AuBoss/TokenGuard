# MinimaxGuard · v0.2.0 桌面打包

## 三种打包方式（按推荐度）

### 1. 🥇 Pake（推荐，最简单）

**适合**：用户已装 Node.js；愿意手动启动 Node 服务

**步骤**：

```bash
# 一次性
npm install
npm install -g pake-cli

# 打包当前平台
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
2. **先启动 Node**（监听 127.0.0.1:5060）：
   ```bash
   ./node dist/index.js --desktop
   # 输出：OPEN_URL=http://127.0.0.1:5060/mobile/<KEY>/
   ```
3. 双击 MinimaxGuard 图标 → 打开 webview → 显示监控面板

### 2. 🥈 Tauri（自包含，~5 MB）

**适合**：要求"双击即用"，不依赖任何外部

**前置**：
```bash
# 安装 Rust
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh
# 安装 Tauri CLI
cargo install tauri-cli
```

**打包**：
```bash
npm run tauri:build
# → src-tauri/target/release/bundle/dmg/*.dmg
# → src-tauri/target/release/bundle/nsis/*.exe
```

**特点**：
- Tauri 主进程启动 Node sidecar binary
- 完全自包含（无需外部 Node）
- 启动稍慢（~1s）但零配置

### 3. 🥉 PWA（不打包）

iPhone Safari → 分享 → "添加到主屏幕" → 桌面图标 → 全屏体验

零编译，零分发，但**不**能离线使用。

## CI/CD 自动发布

详见 `.github/workflows/release.yml`：
- push tag → 自动构建三平台
- 产物作为 GitHub Release Assets 上传

## 文件结构

```
MinimaxGuard/
├── server/              # Node 后端（不变）
├── templates/           # HTML（mobile.html / desktop.html / settings.html）
├── public/              # 静态资源
├── dist/                # TypeScript 编译输出
│
├── pake.config.json     # Pake 配置
├── scripts/
│   └── pake-build.sh    # Pake 打包脚本
├── src-tauri/           # Tauri 项目（高级方案）
│   ├── tauri.conf.json
│   ├── src/main.rs
│   └── icons/
│
├── build/               # 打包产物
│   └── icon.png         # 512x512 应用图标
│
└── config.example.json
```
