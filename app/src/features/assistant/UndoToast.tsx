/**
 * T-M4-02c: 撤销 toast — 执行后 5 秒内可撤销。
 */

import { motion, AnimatePresence } from 'framer-motion'

interface UndoToastProps {
  remaining: number
  onUndo: () => void
}

export function UndoToast({ remaining, onUndo }: UndoToastProps) {
  return (
    <AnimatePresence>
      {remaining > 0 && (
        <motion.div
          className="fixed bottom-20 left-4 right-4 z-50"
          initial={{ y: 100, opacity: 0 }}
          animate={{ y: 0, opacity: 1 }}
          exit={{ y: 100, opacity: 0 }}
          transition={{ type: 'spring', damping: 25, stiffness: 300 }}
        >
          <div
            className="flex items-center justify-between px-4 py-3 rounded-xl"
            style={{
              background: 'var(--nav-bg)',
              backdropFilter: 'blur(12px)',
              border: '1px solid var(--glass-border)',
              boxShadow: '0 4px 12px rgba(0,0,0,0.15)',
            }}
          >
            <span className="text-sm" style={{ color: 'var(--ink)' }}>
              已记录 · {remaining}s 内可撤销
            </span>
            <button
              onClick={onUndo}
              className="px-3 py-1 rounded-lg text-sm font-medium"
              style={{
                background: 'var(--expense, #e74c3c)',
                color: 'white',
              }}
            >
              撤销
            </button>
          </div>
        </motion.div>
      )}
    </AnimatePresence>
  )
}
