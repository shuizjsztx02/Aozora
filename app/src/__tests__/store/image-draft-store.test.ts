import { describe, it, expect, beforeEach } from 'vitest'
import { useImageDraftStore } from '../../platform/store/image-draft-store'

describe('image-draft-store', () => {
  beforeEach(() => {
    // 每个测试前重置 store 状态并清理 localStorage
    useImageDraftStore.setState({ draft: null })
    localStorage.clear()
  })

  it('初始状态为 null', () => {
    const state = useImageDraftStore.getState()
    expect(state.draft).toBeNull()
  })

  it('saveDraft 保存草稿', () => {
    const draft = {
      attachmentId: 'att_123',
      source: 'camera' as const,
      error: 'network',
      createdAt: 1000,
    }
    useImageDraftStore.getState().saveDraft(draft)

    const state = useImageDraftStore.getState()
    expect(state.draft).toEqual(draft)
  })

  it('clearDraft 清除草稿', () => {
    useImageDraftStore.getState().saveDraft({
      attachmentId: 'att_123',
      source: 'gallery',
      error: 'timeout',
      createdAt: 2000,
    })
    expect(useImageDraftStore.getState().draft).not.toBeNull()

    useImageDraftStore.getState().clearDraft()
    expect(useImageDraftStore.getState().draft).toBeNull()
  })

  it('saveDraft 覆盖已有草稿', () => {
    useImageDraftStore.getState().saveDraft({
      attachmentId: 'att_old',
      source: 'camera',
      error: 'network',
      createdAt: 1000,
    })

    useImageDraftStore.getState().saveDraft({
      attachmentId: 'att_new',
      source: 'gallery',
      error: 'timeout',
      createdAt: 2000,
    })

    const draft = useImageDraftStore.getState().draft
    expect(draft).not.toBeNull()
    expect(draft!.attachmentId).toBe('att_new')
    expect(draft!.source).toBe('gallery')
    expect(draft!.error).toBe('timeout')
  })

  it('attachmentId 可为 null（压缩失败时无服务端附件）', () => {
    useImageDraftStore.getState().saveDraft({
      attachmentId: null,
      source: 'camera',
      error: 'compress_failed',
      createdAt: 3000,
    })

    const draft = useImageDraftStore.getState().draft
    expect(draft).not.toBeNull()
    expect(draft!.attachmentId).toBeNull()
    expect(draft!.error).toBe('compress_failed')
  })
})
