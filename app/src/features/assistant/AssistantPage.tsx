/**
 * T-M4-02c: 助手页面 — 对话式记账。
 *
 * 输入 → AI/本地 NLU → 确认卡 → 执行 → 撤销 toast
 */

import { useState, useRef, useEffect, useCallback } from 'react'
import { PageHeader } from '../../components/Layout'
import { useBridge } from '../../platform/hooks/use-bridge'
import { useVoice } from '../../platform/hooks/use-voice'
import { useAssistant } from './use-assistant'
import { ConfirmationCard } from './ConfirmationCard'
import { UndoToast } from './UndoToast'
import type { CategoryData } from '../../platform/bridge/bridge-types'
import type { ActionProposal } from '../../domain/action-proposal'
import { pickEntryBook } from '../../domain/books'

export function AssistantPage() {
  const bridge = useBridge()
  const {
    isRecording: voiceIsRecording,
    partialText: voicePartialText,
    finalText: voiceFinalText,
    error: voiceError,
    startRecording: voiceStartRecording,
    stopRecording: voiceStopRecording,
    reset: voiceReset,
  } = useVoice()
  const [categories, setCategories] = useState<CategoryData[]>([])
  const [bookId, setBookId] = useState<string | null>(null)
  const [inputText, setInputText] = useState('')
  const scrollRef = useRef<HTMLDivElement>(null)
  const prevFinalTextRef = useRef('')

  // 加载分类和账本
  useEffect(() => {
    bridge.listCategories().then(setCategories).catch(() => {})
    bridge.listBooks().then(books => {
      // 决策 #2 / T7-6：聊天记账目标 = 第一个非 master 账本，禁止误选总账本
      const entry = pickEntryBook(books)
      if (entry) setBookId(entry.id)
    }).catch(() => {})
  }, [bridge])

  const {
    messages,
    phase,
    pendingProposal,
    undoRemaining,
    isSlow,
    submitInput,
    confirmEditedProposal,
    cancelProposal,
    undoExecution,
  } = useAssistant({
    categories,
    bookId,
    // C-B4 fix: 使用设备时区替代硬编码
    timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || 'Asia/Shanghai',
  })

  // 自动滚动到底部
  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: 'smooth' })
  }, [messages, phase])

  // T-M4-04b: 转写文本 → 填入输入框
  useEffect(() => {
    if (voiceFinalText && voiceFinalText !== prevFinalTextRef.current) {
      prevFinalTextRef.current = voiceFinalText
      setInputText(voiceFinalText)
      voiceReset()
    }
  }, [voiceFinalText, voiceReset])

  const handleSubmit = useCallback(async () => {
    if (!inputText.trim() || phase === 'processing' || phase === 'executing') return
    const text = inputText.trim()
    setInputText('')
    await submitInput(text)
  }, [inputText, phase, submitInput])

  const handleKeyDown = useCallback((e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSubmit()
    }
  }, [handleSubmit])

  const handleConfirmEdited = useCallback((edited: ActionProposal) => {
    confirmEditedProposal(edited)
  }, [confirmEditedProposal])

  // T-M4-04b: 语音输入控制
  const handleMicClick = useCallback(async () => {
    if (voiceIsRecording) {
      await voiceStopRecording()
    } else {
      await voiceStartRecording()
    }
  }, [voiceIsRecording, voiceStartRecording, voiceStopRecording])

  return (
    <div className="flex flex-col h-screen safe-top" style={{ background: 'var(--bg)' }}>
      <PageHeader back />

      {/* 消息列表 */}
      <div
        ref={scrollRef}
        className="flex-1 overflow-y-auto px-4 py-3"
      >
        {messages.length === 0 && (
          <div className="flex items-center justify-center h-full">
            <div className="text-center">
              <div className="text-4xl mb-3">💬</div>
              <p className="text-sm" style={{ color: 'var(--ink2)' }}>
                输入记账短句，如「午饭 12.5」
              </p>
              <p className="text-xs mt-1" style={{ color: 'var(--ink3)' }}>
                支持 AI 理解，断网自动降级本地解析
              </p>
            </div>
          </div>
        )}

        {messages.map(msg => (
          <div
            key={msg.id}
            className={`flex mb-3 ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}
          >
            <div
              className="max-w-[80%] px-3 py-2 rounded-xl text-sm"
              style={{
                background: msg.role === 'user'
                  ? 'var(--accent)'
                  : 'var(--glass)',
                color: msg.role === 'user'
                  ? 'white'
                  : 'var(--ink)',
                border: msg.role === 'user'
                  ? 'none'
                  : '1px solid var(--glass-border)',
              }}
            >
              <div>{msg.text}</div>
              {msg.proposalStatus === 'error' && (
                <div className="text-xs mt-1 opacity-70">执行失败</div>
              )}
              {msg.proposalStatus === 'undone' && (
                <div className="text-xs mt-1 opacity-70">已撤销</div>
              )}
            </div>
          </div>
        ))}

        {/* 慢速提示 */}
        {isSlow && phase === 'processing' && (
          <div className="flex justify-start mb-3">
            <div
              className="px-3 py-2 rounded-xl text-xs"
              style={{
                background: 'var(--glass)',
                color: 'var(--ink3)',
                border: '1px solid var(--glass-border)',
              }}
            >
              ⏳ 正在处理中，请稍候…
            </div>
          </div>
        )}

        {/* 处理中动画 */}
        {phase === 'processing' && !isSlow && (
          <div className="flex justify-start mb-3">
            <div
              className="px-3 py-2 rounded-xl text-sm"
              style={{
                background: 'var(--glass)',
                color: 'var(--ink2)',
                border: '1px solid var(--glass-border)',
              }}
            >
              思考中…
            </div>
          </div>
        )}

        {/* 确认卡 — confirming 和 executing 阶段都显示 */}
        {(phase === 'confirming' || phase === 'executing') && pendingProposal && (
          <ConfirmationCard
            proposal={pendingProposal}
            categories={categories}
            onConfirm={handleConfirmEdited}
            onCancel={cancelProposal}
            submitting={phase === 'executing'}
            error={messages.filter(m => m.proposalStatus === 'error').pop()?.text || null}
          />
        )}
      </div>

      {/* 输入区 */}
      <div
        className="px-4 py-3"
        style={{
          background: 'var(--nav-bg)',
          borderTop: '1px solid var(--glass-border)',
        }}
      >
        {/* T-M4-04b: 实时部分转写提示 */}
        {voiceIsRecording && voicePartialText && (
          <div
            className="mb-2 px-3 py-1.5 rounded-lg text-xs truncate"
            style={{
              background: 'var(--glass)',
              color: 'var(--ink2)',
              border: '1px solid var(--glass-border)',
            }}
            data-testid="voice-partial"
          >
            🎤 {voicePartialText}
          </div>
        )}
        {/* T-M4-04b: 语音错误提示 */}
        {voiceError && !voiceIsRecording && (
          <div
            className="mb-2 px-3 py-1.5 rounded-lg text-xs"
            style={{
              background: '#fef2f2',
              color: '#b91c1c',
              border: '1px solid #fecaca',
            }}
            data-testid="voice-error"
          >
            {voiceError.message}
          </div>
        )}
        <div className="flex gap-2 items-end">
          <input
            type="text"
            value={inputText}
            onChange={e => setInputText(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder={voiceIsRecording ? '正在聆听…' : '输入记账短句…'}
            disabled={phase === 'processing' || phase === 'executing'}
            className="flex-1 px-3 py-2 rounded-xl text-sm outline-none"
            style={{
              background: 'var(--glass)',
              color: 'var(--ink)',
              border: '1px solid var(--glass-border)',
            }}
          />
          {/* T-M4-04b: 语音输入按钮 */}
          <button
            onClick={handleMicClick}
            disabled={phase === 'processing' || phase === 'executing'}
            className="px-3 py-2 rounded-xl text-sm transition-all disabled:opacity-40"
            style={{
              background: voiceIsRecording ? '#ef4444' : 'var(--glass)',
              color: voiceIsRecording ? 'white' : 'var(--ink2)',
              border: voiceIsRecording
                ? '1px solid #dc2626'
                : '1px solid var(--glass-border)',
              minWidth: '40px',
            }}
            title={voiceIsRecording ? '停止录音' : '语音输入'}
            data-testid="voice-mic-button"
            aria-label={voiceIsRecording ? '停止录音' : '语音输入'}
          >
            {voiceIsRecording ? '⏹' : '🎤'}
          </button>
          <button
            onClick={handleSubmit}
            disabled={!inputText.trim() || phase === 'processing' || phase === 'executing'}
            className="px-4 py-2 rounded-xl text-sm font-medium text-white transition-opacity disabled:opacity-40"
            style={{ background: 'var(--accent)' }}
          >
            发送
          </button>
        </div>
      </div>

      {/* 撤销 toast */}
      <UndoToast
        remaining={undoRemaining}
        onUndo={undoExecution}
      />
    </div>
  )
}
