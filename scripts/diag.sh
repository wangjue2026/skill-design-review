#!/bin/bash
# ============================================================
# diag.sh — 设计检视工具链环境诊断脚本
#
# 用法: bash scripts/diag.sh
#
# 检测项：
#   1. Node.js 版本
#   2. npm install (puppeteer 安装)
#   3. 浏览器拉起 + 截图
#   4. Python3 + Pillow 可用性
#   5. auto_annotate.py 标注功能
# ============================================================

set -e  # 遇到非预期错误时中止

PASS="✅"
FAIL="❌"
SKIP="⏭️ "
DIVIDER="────────────────────────────────────────"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
ASSETS_DIR="$PROJECT_DIR/Reports/assets"
DIAG_SCREENSHOT="$ASSETS_DIR/_diag_screenshot.png"
DIAG_ANNOTATED="$ASSETS_DIR/_diag_annotated.png"

echo ""
echo "🔍 设计检视工具链诊断"
echo "$DIVIDER"

# ── 1. Node.js 版本 ───────────────────────────────────────────
echo ""
echo "[1/5] Node.js 版本"
if command -v node &>/dev/null; then
  NODE_VER=$(node --version)
  echo "  $PASS Node.js $NODE_VER"
else
  echo "  $FAIL Node.js 未安装，请先安装 Node.js >= 18"
  exit 1
fi

# ── 2. Puppeteer 安装 ─────────────────────────────────────────
echo ""
echo "[2/5] Puppeteer 安装 (npm install)"
cd "$PROJECT_DIR"
if node -e "require('puppeteer')" 2>/dev/null; then
  PPTR_VER=$(node -e "console.log(require('puppeteer/package.json').version)" 2>/dev/null || echo "未知版本")
  echo "  $PASS puppeteer v$PPTR_VER 已就绪"
else
  echo "  ⏳ 正在安装 puppeteer..."
  npm install --silent
  if node -e "require('puppeteer')" 2>/dev/null; then
    echo "  $PASS puppeteer 安装成功"
  else
    echo "  $FAIL puppeteer 安装失败，请检查网络或权限"
    exit 1
  fi
fi

# ── 3. 浏览器拉起 + 截图 ──────────────────────────────────────
echo ""
echo "[3/5] 浏览器拉起 + 截图"
mkdir -p "$ASSETS_DIR"

node - <<EOF
const puppeteer = require('puppeteer');
const path = require('path');

(async () => {
  let browser;
  try {
    console.log('  ⏳ 启动 Chromium...');
    browser = await puppeteer.launch({
      headless: true,
      args: ['--no-sandbox', '--disable-setuid-sandbox', '--disable-dev-shm-usage']
    });
    const page = await browser.newPage();
    await page.setViewport({ width: 1280, height: 720 });

    console.log('  ⏳ 导航至测试页面...');
    await page.goto('https://example.com', { waitUntil: 'domcontentloaded', timeout: 15000 });

    const screenshotPath = '${DIAG_SCREENSHOT}';
    await page.screenshot({ path: screenshotPath });
    console.log('  ✅ 浏览器拉起成功');
    console.log('  ✅ 截图已生成：' + screenshotPath);
  } catch (e) {
    console.error('  ❌ 失败：' + e.message);
    process.exit(1);
  } finally {
    if (browser) await browser.close();
  }
})();
EOF

# ── 4. Python3 + Pillow ───────────────────────────────────────
echo ""
echo "[4/5] Python3 + Pillow"
if command -v python3 &>/dev/null; then
  PY_VER=$(python3 --version)
  echo "  $PASS $PY_VER"
  if python3 -c "from PIL import Image" 2>/dev/null; then
    PIL_VER=$(python3 -c "import PIL; print(PIL.__version__)" 2>/dev/null || echo "未知版本")
    echo "  $PASS Pillow v$PIL_VER 已就绪"
  else
    echo "  ⏳ 正在安装 Pillow..."
    pip3 install pillow --quiet
    if python3 -c "from PIL import Image" 2>/dev/null; then
      echo "  $PASS Pillow 安装成功"
    else
      echo "  $FAIL Pillow 安装失败，标注功能不可用"
      exit 1
    fi
  fi
else
  echo "  $FAIL Python3 未安装，标注功能不可用"
  exit 1
fi

# ── 5. auto_annotate.py 标注功能 ──────────────────────────────
echo ""
echo "[5/5] 截图标注功能 (auto_annotate.py)"
if [ -f "$DIAG_SCREENSHOT" ]; then
  ANNOTATION='[{"rect":[20,20,400,80],"label":"[DIAG] 标注功能测试 OK","color":"red"}]'
  python3 "$SCRIPT_DIR/auto_annotate.py" "$DIAG_SCREENSHOT" "$ANNOTATION"
  if [ $? -eq 0 ]; then
    # auto_annotate.py 输出到 _annotated 文件，重命名为诊断产出
    ANNOTATED_SOURCE="${DIAG_SCREENSHOT%.png}_annotated.png"
    if [ -f "$ANNOTATED_SOURCE" ]; then
      mv "$ANNOTATED_SOURCE" "$DIAG_ANNOTATED"
      echo "  $PASS 标注成功：$DIAG_ANNOTATED"
    else
      echo "  $PASS 标注执行成功（输出文件路径请确认）"
    fi
  else
    echo "  $FAIL 标注失败，请检查 auto_annotate.py"
    exit 1
  fi
else
  echo "  $SKIP 跳过（截图文件不存在，第3步可能失败）"
fi

# ── 总结 ──────────────────────────────────────────────────────
echo ""
echo "$DIVIDER"
echo "🎉 全部检测通过！工具链完整可用。"
echo ""
echo "诊断产出文件（可删除）："
echo "  - $DIAG_SCREENSHOT"
echo "  - $DIAG_ANNOTATED"
echo ""
echo "下一步：参考 README.md 开始你的第一次设计检视任务"
echo ""
