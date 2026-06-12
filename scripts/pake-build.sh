#!/bin/bash
# ===================================================================
# Pake 打包脚本 - 快速打包 Mac/Win/Linux 桌面应用
# ===================================================================
# 前提：
#   1. 已安装 Node 18+
#   2. 已 npm install
#   3. 已安装 Pake: npm install -g pake-cli
# ===================================================================

set -e

PACKAGE_VERSION=$(node -p "require('./package.json').version")
PACKAGE_NAME=$(node -p "require('./package.json').name")
BUILD_DIR="build"
ICON="$BUILD_DIR/icon.png"

echo "==> Building TypeScript..."
npm run build

if [ ! -f "$ICON" ]; then
  echo "==> No icon at $ICON, using default (you should provide 512x512 PNG)"
  mkdir -p "$BUILD_DIR"
  # 生成一个简单的占位 icon（实心黑色方块 + 白字）
  # 实际项目应替换为品牌 icon
  echo "  Please provide build/icon.png (512x512)"
  exit 1
fi

# 桌面模式启动 URL（Node 监听 127.0.0.1 随机端口）
# Pake 会以 webview 打开此 URL
START_URL="http://127.0.0.1:5060/mobile"

# ============== Mac ==============
if [[ "$OSTYPE" == "darwin"* ]] || [ -n "$BUILD_MAC" ]; then
  echo "==> Building macOS..."
  pake build \
    --packager dmg \
    --targets universal \
    --name "$PACKAGE_NAME" \
    --icon "$ICON" \
    --height 932 --width 432 \
    --user-agent "MinimaxGuard/Desktop" \
    --identifier "com.auboss.tokenguard" \
    --version "$PACKAGE_VERSION" \
    --category "DeveloperTool" \
    --copyright "MIT" \
    "$START_URL"
fi

# ============== Windows ==============
if [[ "$OSTYPE" == "msys"* ]] || [ -n "$BUILD_WIN" ]; then
  echo "==> Building Windows..."
  pake build \
    --packager nsis \
    --targets x64 \
    --name "$PACKAGE_NAME" \
    --icon "$ICON" \
    --height 932 --width 432 \
    --user-agent "MinimaxGuard/Desktop" \
    --identifier "com.auboss.tokenguard" \
    --version "$PACKAGE_VERSION" \
    --category "DeveloperTool" \
    --copyright "MIT" \
    "$START_URL"
fi

# ============== Linux ==============
if [[ "$OSTYPE" == "linux"* ]] || [ -n "$BUILD_LINUX" ]; then
  echo "==> Building Linux..."
  pake build \
    --packager deb \
    --targets x64 \
    --name "$PACKAGE_NAME" \
    --icon "$ICON" \
    --height 932 --width 432 \
    --user-agent "MinimaxGuard/Desktop" \
    --identifier "com.auboss.tokenguard" \
    --version "$PACKAGE_VERSION" \
    --category "DeveloperTool" \
    --copyright "MIT" \
    "$START_URL"
fi

echo "==> Done!"
echo "    Output: $(pwd)/$(ls -t *.dmg *.exe *.deb *.AppImage 2>/dev/null | head -1)"
