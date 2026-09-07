/**
 * T-M4-04d: 图片来源选择底部弹层。
 *
 * 提供拍照 / 从相册选择两个入口，集成权限预检。
 */

import { useCallback, useState, useEffect, useRef } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { usePermissions } from '../../platform/hooks/use-permissions'

interface ImagePickerSheetProps {
  onPick: (source: 'camera' | 'gallery') => void
  onClose: () => void
}

export function ImagePickerSheet({ onPick, onClose }: ImagePickerSheetProps) {
  const { canCamera, canGallery, requestPermission, openSettings, shouldGuideSettings } = usePermissions()
  const [pendingSource, setPendingSource] = useState<'camera' | 'gallery' | null>(null)
  // 用 ref 持有最新 onPick，避免 effect 依赖不稳定（父组件每次 image/draft 变化都重建 handleImagePick）
  const onPickRef = useRef(onPick)
  onPickRef.current = onPick

  const handlePick = useCallback(
    async (source: 'camera' | 'gallery') => {
      const hasPermission = source === 'camera' ? canCamera : canGallery
      const perm = source === 'camera' ? 'CAMERA' as const : 'READ_MEDIA_IMAGES' as const

      if (!hasPermission) {
        // R-M4-48 fix: 记录待处理源，权限授予后自动续接
        setPendingSource(source)
        await requestPermission(perm)
        return
      }

      onPick(source)
    },
    [canCamera, canGallery, requestPermission, onPick],
  )

  // R-M4-48 fix: 权限授予后自动续接选择
  useEffect(() => {
    if (!pendingSource) return
    const hasPermission = pendingSource === 'camera' ? canCamera : canGallery
    if (hasPermission) {
      onPickRef.current(pendingSource)
      setPendingSource(null)
    }
  }, [pendingSource, canCamera, canGallery])

  return (
    <AnimatePresence>
      <motion.div
        className="fixed inset-0 z-50 flex flex-col justify-end"
        data-testid="image-picker-sheet"
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        exit={{ opacity: 0 }}
        transition={{ duration: 0.18 }}
      >
        {/* 遮罩 */}
        <div className="absolute inset-0 bg-black/30" onClick={onClose} />

        {/* Sheet 内容 */}
        <motion.div
          className="sheet-glass relative z-10 pb-6 px-4"
          initial={{ y: '100%' }}
          animate={{ y: 0 }}
          exit={{ y: '100%' }}
          transition={{ type: 'spring', damping: 34, stiffness: 380 }}
        >
          {/* 拖拽指示器 */}
          <div className="flex justify-center py-3">
            <div className="w-10 h-1 rounded-full" style={{ background: 'var(--ink3)', opacity: 0.3 }} />
          </div>

          <div className="text-base font-medium mb-4 text-center" style={{ color: 'var(--ink)' }}>
            添加图片
          </div>

          {/* 权限引导（永久拒绝时显示） */}
          {shouldGuideSettings && (
            <div
              className="text-xs text-center mb-3 px-3 py-2 rounded-lg"
              style={{ background: 'var(--glass)', color: 'var(--ink2)', border: '1px solid var(--glass-border)' }}
            >
              请在系统设置中允许访问相机和相册
              <button
                onClick={openSettings}
                className="ml-2 font-medium"
                style={{ color: 'var(--accent)' }}
              >
                去设置
              </button>
            </div>
          )}

          {/* 拍照 */}
          <button
            onClick={() => handlePick('camera')}
            className="w-full flex items-center gap-4 py-3.5 px-4 rounded-xl mb-2 transition-colors"
            style={{ background: 'var(--glass)', border: '1px solid var(--glass-border)' }}
            data-testid="pick-camera"
          >
            <span className="text-2xl">📷</span>
            <span className="text-sm" style={{ color: 'var(--ink)' }}>拍照</span>
          </button>

          {/* 从相册选择 */}
          <button
            onClick={() => handlePick('gallery')}
            className="w-full flex items-center gap-4 py-3.5 px-4 rounded-xl mb-4 transition-colors"
            style={{ background: 'var(--glass)', border: '1px solid var(--glass-border)' }}
            data-testid="pick-gallery"
          >
            <span className="text-2xl">🖼️</span>
            <span className="text-sm" style={{ color: 'var(--ink)' }}>从相册选择</span>
          </button>

          {/* 取消 */}
          <button
            onClick={onClose}
            className="w-full py-3 rounded-xl text-sm font-medium transition-colors"
            style={{ color: 'var(--ink2)', border: '1px solid var(--glass-border)' }}
            data-testid="pick-cancel"
          >
            取消
          </button>
        </motion.div>
      </motion.div>
    </AnimatePresence>
  )
}
