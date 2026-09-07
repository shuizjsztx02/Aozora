/**
 * T-M4-04d + T-M4-04e: 图片上传/解析草稿 store。
 *
 * 持久化到 localStorage，用于上传失败/解析失败后的跨会话恢复。
 * 保存最近一次图片操作的源、附件 ID、错误码和解析结果。
 */

import { create } from 'zustand'
import { persist } from 'zustand/middleware'
import type { ImageSource, ImagePickResult } from '../bridge/bridge-types'

export interface ImageDraft {
  /** 已上传的服务端附件 ID（上传成功时有值，用于清理孤儿附件） */
  attachmentId: string | null
  /** T-M4-04e fix: 完整附件信息（用于草稿恢复后 ImageUploadView 能显示缩略图） */
  attachment?: ImagePickResult | null
  /** 选择的源 */
  source: ImageSource
  /** 最近一次错误码（null = 无错误/已成功） */
  error: string | null
  /** T-M4-04e: 解析失败错误码（与 error 分开，区分上传失败和解析失败） */
  parseError?: string | null
  /** T-M4-04e: 解析成功后保存的 ActionProposal（用于恢复批量确认卡） */
  proposal?: Record<string, unknown> | null
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
