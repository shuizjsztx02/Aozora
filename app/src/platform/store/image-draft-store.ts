/**
 * T-M4-04d: 图片上传草稿 store。
 *
 * 持久化到 localStorage，用于上传失败后的跨会话恢复。
 * 保存最近一次图片操作的源、附件 ID 与错误码。
 */

import { create } from 'zustand'
import { persist } from 'zustand/middleware'
import type { ImageSource } from '../bridge/bridge-types'

export interface ImageDraft {
  /** 已上传的服务端附件 ID（上传成功时有值，用于清理孤儿附件） */
  attachmentId: string | null
  /** 选择的源 */
  source: ImageSource
  /** 最近一次错误码（null = 无错误/已成功） */
  error: string | null
  /** 草稿创建时间戳 */
  createdAt: number
}

interface ImageDraftState {
  draft: ImageDraft | null
  saveDraft: (draft: ImageDraft) => void
  clearDraft: () => void
}

export const useImageDraftStore = create<ImageDraftState>()(
  persist(
    (set) => ({
      draft: null,
      saveDraft: (draft) => set({ draft }),
      clearDraft: () => set({ draft: null }),
    }),
    { name: 'aozora-image-draft' },
  ),
)

/** Selector hook */
export function useImageDraft() {
  return useImageDraftStore(s => s.draft)
}
