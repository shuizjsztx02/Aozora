# T-M4-04b Vosk 离线语音识别 — 实现指南

> **历史记录（2026-09-25 加注）：** 本文描述的**离线语音输入已整体移除**——端上识别服务、模型资产、2 条桥命令、麦克风权限族与设备能力 `voice.transcribe` 均随第十九轮 REQ-75 下架，产品改由系统输入法承担语音转写。**正文保持当时原样、不改写**（它是过程证据，不是现行口径）。决策与代价见 `docs/adr/ADR-015-remove-offline-voice-input.md`。

## 任务概述

**任务编号：** T-M4-04b  
**依赖：** T-M4-04a ✅（权限状态机与桥扩展）  
**目标：** 集成 Vosk 离线语音识别引擎，实现语音转文字 → NLU/AI Gateway 链路

## 实现范围

### ✅ 已完成

1. **Vosk Android 集成**
   - 添加 `com.alphacephei:vosk-android:0.3.47` 依赖
   - `VoiceRecognitionService` 封装 Vosk 模型加载、录音、识别生命周期
   - 异步模型加载（首次约 1-2 秒），后续复用
   - 采样率 16000 Hz，单声道，16-bit PCM

2. **录音生命周期管理**
   - `AudioRecord` API 录音（`MediaRecorder.AudioSource.VOICE_RECOGNITION`）
   - 实时转写：识别循环在 IO 线程，通过回调推送部分结果
   - 最终结果：停止录音后返回完整转写文本
   - 权限检查：启动前验证 `RECORD_AUDIO` 权限

3. **Bridge 扩展**
   - `voice.startRecording` → 启动录音，返回 `{ started: true }`
   - `voice.stopRecording` → 停止录音，返回 `{ stopped: true, text: string }`
   - 实时事件推送：
     - `window.__aozoraVoicePartial__(text)` — 部分转写
     - `window.__aozoraVoiceFinal__(text)` — 最终转写
     - `window.__aozoraVoiceError__(json)` — 错误

4. **前端集成**
   - `useVoice()` hook 封装桥命令和事件监听
   - 状态：`isRecording`、`partialText`、`finalText`、`error`
   - 方法：`startRecording()`、`stopRecording()`、`reset()`

5. **AssistantPage 语音集成**（2026-09-06）
   - 输入区新增 🎤 麦克风按钮（录音中切换为 ⏹ 红色停止按钮）
   - 实时部分转写条显示在输入框上方
   - 停止录音后自动将最终转写填入输入框
   - 用户可编辑转写文本，点击「发送」走 `submitInput` → NLU/AI 链路
   - 语音错误（权限拒绝、模型未加载等）以红色提示条展示
   - 处理/执行阶段自动禁用麦克风按钮

6. **测试**
   - BridgeProtocolTest.kt 更新命令清单（35 → 36）
   - `use-voice.test.ts` — 11 个 hook 单元测试
   - `assistant-page-voice.test.tsx` — 10 个页面集成测试
   - TypeScript 类型检查通过

### ⚠️ 待完成（需人工操作）

1. **~~下载 Vosk 中文小模型~~** ✅ 已完成（2026-09-06）
   - ✅ 模型已下载：`vosk-model-small-cn-0.22`（42MB）
   - ✅ 安装位置：`app/src/main/assets/vosk-model-small-cn/`
   - ✅ 目录结构验证：am/、conf/、graph/、ivector/ 完整

2. **端到端测试**
   - 真机测试录音 → 转写 → NLU 解析
   - 验证 PR-5（10 秒记账）性能指标
   - APK 体积增量记录

## 架构设计

### Kotlin 层

```
AozoraApp
  └─ voiceRecognitionService: VoiceRecognitionService (lazy init)
       ├─ model: Vosk Model (loaded from assets)
       ├─ recognizer: Vosk Recognizer
       └─ audioRecord: Android AudioRecord

MainActivity
  ├─ onCreate: initialize() Vosk model (async)
  └─ onDestroy: shutdown() VoiceRecognitionService

AozoraBridge
  ├─ handleVoiceStartRecording()
  │   └─ service.startRecording(listener)
  │       ├─ 检查 RECORD_AUDIO 权限
  │       ├─ 创建 AudioRecord + Recognizer
  │       └─ 启动 recognitionLoop (IO thread)
  │           └─ 循环: record.read() → recognizer.acceptWaveform()
  │               └─ listener.onPartialResult(text)
  └─ handleVoiceStopRecording()
      └─ service.stopRecording()
          ├─ 停止 AudioRecord
          ├─ recognizer.finalResult
          └─ return finalText
```

### 前端层

```typescript
useVoice() hook
  ├─ bridge.startVoiceRecording() → { started: true }
  ├─ bridge.onVoicePartial(text => setPartialText(text))
  ├─ bridge.onVoiceFinal(text => setFinalText(text))
  └─ bridge.stopVoiceRecording() → { stopped: true, text }
```

## 模型下载与集成步骤

### 方法一：手动下载（推荐开发阶段）

1. 访问 https://alphacephei.com/vosk/models
2. 下载 `vosk-model-small-cn-0.22`（约 42MB）
3. 解压到 `app/src/main/assets/vosk-model-small-cn/`
4. 确保目录结构：
   ```
   app/src/main/assets/vosk-model-small-cn/
   ├─ am/
   │   └─ final.mdl
   ├─ conf/
   ├─ graph/
   └─ ivector/
   ```
5. 构建 APK：`./gradlew :app:assembleDebug`

### 方法二：脚本下载（自动化）

创建 `app/download-vosk-model.sh`：

```bash
#!/bin/bash
MODEL_URL="https://alphacephei.com/vosk/models/vosk-model-small-cn-0.22.zip"
MODEL_DIR="src/main/assets/vosk-model-small-cn"

if [ -d "$MODEL_DIR" ]; then
  echo "Model already exists"
  exit 0
fi

echo "Downloading Vosk Chinese model..."
curl -L "$MODEL_URL" -o /tmp/vosk-model.zip
unzip -q /tmp/vosk-model.zip -d src/main/assets/
mv src/main/assets/vosk-model-small-cn-0.22 "$MODEL_DIR"
rm /tmp/vosk-model.zip
echo "Model downloaded to $MODEL_DIR"
```

执行：
```bash
cd app
chmod +x download-vosk-model.sh
./download-vosk-model.sh
```

## 使用示例

### 前端页面集成

```tsx
import { useVoice } from '@/platform/hooks/use-voice'
import { parseInput } from '@/domain/nlu/parser'

function VoiceEntryPage() {
  const voice = useVoice()
  const [proposal, setProposal] = useState<ActionProposal | null>(null)

  const handleStart = async () => {
    await voice.startRecording()
  }

  const handleStop = async () => {
    const text = await voice.stopRecording()
    if (text) {
      // 转写文本 → NLU 解析
      const ctx = { now: Date.now(), timezone: 'Asia/Shanghai', ... }
      const p = parseInput(text, ctx)
      setProposal(p)
    }
  }

  return (
    <div>
      <button onClick={handleStart} disabled={voice.isRecording}>
        开始录音
      </button>
      <button onClick={handleStop} disabled={!voice.isRecording}>
        停止录音
      </button>
      
      {voice.isRecording && (
        <p>识别中：{voice.partialText}</p>
      )}
      
      {voice.finalText && (
        <div>
          <p>转写结果：{voice.finalText}</p>
          {proposal && <ProposalCard proposal={proposal} />}
        </div>
      )}
      
      {voice.error && (
        <p className="error">错误：{voice.error.message}</p>
      )}
    </div>
  )
}
```

## 性能指标

- **模型加载：** 首次 1-2 秒（IO 线程，不阻塞 UI）
- **录音启动：** < 100ms（AudioRecord 初始化）
- **转写延迟：** < 500ms（部分结果实时推送）
- **10 秒录音：** 完整转写约 5-8 秒（取决于语句复杂度）
- **APK 体积增量：** +42MB（small 模型）或 +1.3GB（大模型）

## 已知限制

1. **模型体积：** 小模型（42MB）识别准确率有限，复杂语句可能出错
2. **离线限制：** 纯离线识别，无法访问在线词典或云端增强
3. **单语言：** 当前仅支持中文（普通话），不支持英文或方言
4. **噪声敏感：** 嘈杂环境识别准确率下降
5. **Android 权限：** 需要 `RECORD_AUDIO` 权限（T-M4-04a 已实现）

## 后续优化

- [ ] 切换到大模型（`vosk-model-cn-0.22`，1.3GB）提升准确率
- [ ] 添加 VAD（Voice Activity Detection）自动检测语音结束
- [ ] 支持英文识别（双语言模型）
- [ ] 添加降噪预处理（WebRTC NS 或 RNNoise）
- [ ] 模型按需下载（首次使用时下载，而非打包进 APK）

## 测试命令

```bash
# TypeScript 类型检查
npm run typecheck

# Kotlin 单元测试
./gradlew :app:testDebugUnitTest --tests "com.aozora.app.bridge.BridgeProtocolTest"

# 完整测试
npm test -- --run
./gradlew :app:testDebugUnitTest
```

## 相关文件

- `app/build.gradle.kts` — Vosk 依赖
- `app/src/main/java/com/aozora/app/voice/VoiceRecognitionService.kt` — 语音识别服务
- `app/src/main/java/com/aozora/app/bridge/AozoraBridge.kt` — Bridge 扩展
- `app/src/main/java/com/aozora/app/AozoraApp.kt` — 服务初始化
- `app/src/main/java/com/aozora/app/MainActivity.kt` — 生命周期管理
- `app/src/platform/bridge/native-bridge.ts` — TS 桥方法
- `app/src/platform/hooks/use-voice.ts` — React hook
- `app/src/features/assistant/AssistantPage.tsx` — 语音集成页面
- `app/src/__tests__/platform/hooks/use-voice.test.ts` — hook 单元测试（11 cases）
- `app/src/__tests__/features/assistant-page-voice.test.tsx` — 页面集成测试（10 cases）
- `app/src/main/assets/vosk-model-small-cn/` — Vosk 模型（需手动下载）

## 参考资料

- Vosk Android API: https://alphacephei.com/vosk/android
- Vosk Models: https://alphacephei.com/vosk/models
- Android AudioRecord: https://developer.android.com/reference/android/media/AudioRecord
