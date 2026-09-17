#!/bin/bash
#
# T-M4-04b: 下载 Vosk 中文小模型
#
# 用法: ./download-vosk-model.sh
#
# 模型信息:
# - 名称: vosk-model-small-cn-0.22
# - 大小: ~42MB
# - 语言: 中文（普通话）
# - 来源: https://alphacephei.com/vosk/models
#

set -e

MODEL_URL="https://alphacephei.com/vosk/models/vosk-model-small-cn-0.22.zip"
MODEL_NAME="vosk-model-small-cn-0.22"
TARGET_DIR="src/main/assets/vosk-model-small-cn"

echo "=== Vosk 中文小模型下载脚本 ==="
echo ""

# 检查目标目录
if [ -d "$TARGET_DIR" ]; then
    echo "✅ 模型已存在于: $TARGET_DIR"
    echo "如需重新下载，请先删除该目录"
    exit 0
fi

echo "📦 模型信息:"
echo "   名称: $MODEL_NAME"
echo "   大小: ~42MB"
echo "   语言: 中文（普通话）"
echo "   目标: $TARGET_DIR"
echo ""

# 创建临时目录
TEMP_DIR=$(mktemp -d)
echo "📁 临时目录: $TEMP_DIR"

# 下载模型
echo ""
echo "⬇️  正在下载模型..."
if command -v curl &> /dev/null; then
    curl -L "$MODEL_URL" -o "$TEMP_DIR/model.zip"
elif command -v wget &> /dev/null; then
    wget -O "$TEMP_DIR/model.zip" "$MODEL_URL"
else
    echo "❌ 错误: 需要 curl 或 wget"
    exit 1
fi

# 解压
echo ""
echo "📂 正在解压..."
unzip -q "$TEMP_DIR/model.zip" -d "$TEMP_DIR"

# 移动到目标位置
echo "📦 正在安装到 assets..."
mkdir -p "src/main/assets"
mv "$TEMP_DIR/$MODEL_NAME" "$TARGET_DIR"

# 清理
rm -rf "$TEMP_DIR"

echo ""
echo "✅ 完成！模型已安装到: $TARGET_DIR"
echo ""
echo "📋 目录结构:"
ls -la "$TARGET_DIR" | head -10
echo ""
echo "🚀 下一步:"
echo "   1. 构建 APK: ./gradlew :app:assembleDebug"
echo "   2. 安装到设备并测试语音识别"
echo ""
echo "💡 提示:"
echo "   - 如需更高准确率，可下载大模型 (1.3GB):"
echo "     https://alphacephei.com/vosk/models/vosk-model-cn-0.22.zip"
echo "   - 修改 VoiceRecognitionService.kt 中的 MODEL_DIR 常量"
