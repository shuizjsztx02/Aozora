/**
 * PeriodPanel — 年 / 月选择弹层（共享组件）。
 *
 * 从 DetailPage 原样搬出（T10-5 / REQ-21）：fixed 遮罩 + glass-card 绝对定位，
 * 年份 ‹ › 切换 + 12 月宫格。`pickerYear` 与 `period` 分离——前者仅控制面板
 * 显示的年份，后者为实际选中值；调用方决定点月份后的行为（例如关闭弹层）。
 *
 * DetailPage（明细页期间行）、ReportsPage（报表期间行）共用。
 */

interface PeriodPanelProps {
  open: boolean
  onClose: () => void
  /** 当前面板显示的年份（受控，切 ‹ › 时回调） */
  year: number
  onYearChange: (year: number) => void
  /** 当前选中期间 'YYYY-MM'（高亮用） */
  period: string
  /** 点月份回调；调用方负责关闭弹层（若需要） */
  onPick: (period: string) => void
}

export function PeriodPanel({ open, onClose, year, onYearChange, period, onPick }: PeriodPanelProps) {
  if (!open) return null
  return (
    <>
      <div className="fixed inset-0 z-10" onClick={onClose} aria-hidden />
      <div
        className="glass-card absolute left-4 right-4 top-full z-20 mt-1 p-3"
        style={{ boxShadow: 'var(--card-shadow)' }}
      >
        <div className="flex items-center justify-between mb-2">
          <button
            onClick={() => onYearChange(year - 1)}
            className="w-8 h-8 rounded-full flex items-center justify-center active:opacity-60"
            style={{ color: 'var(--ink)' }}
            aria-label="上一年"
          >
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round"><polyline points="15 18 9 12 15 6" /></svg>
          </button>
          <span className="text-[15px] font-semibold" style={{ color: 'var(--ink)' }}>{year}年</span>
          <button
            onClick={() => onYearChange(year + 1)}
            className="w-8 h-8 rounded-full flex items-center justify-center active:opacity-60"
            style={{ color: 'var(--ink)' }}
            aria-label="下一年"
          >
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round"><polyline points="9 18 15 12 9 6" /></svg>
          </button>
        </div>
        <div className="grid grid-cols-4 gap-2">
          {Array.from({ length: 12 }, (_, i) => i + 1).map(m => {
            const p = `${year}-${String(m).padStart(2, '0')}`
            const active = p === period
            return (
              <button
                key={m}
                onClick={() => onPick(p)}
                className="py-2 rounded-lg text-sm font-medium transition-colors"
                style={{
                  background: active ? 'var(--accent)' : 'var(--glass)',
                  color: active ? 'white' : 'var(--ink2)',
                }}
              >
                {m}月
              </button>
            )
          })}
        </div>
      </div>
    </>
  )
}
