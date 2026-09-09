/**
 * 自绘日期输入（第十轮 REQ-18 / 设计决策 #2）。
 *
 * 替换系统 `<input type="date">`：Android WebView 弹 Material 对话框，
 * 样式与语言均不可控。本组件为 App 风格中文日历弹层。
 *
 * 值语义与原生控件一致：受控 ISO 'YYYY-MM-DD' 字符串，onChange 只会
 * 给出合法日期（点日格/回今天），宿主无需再做清空兜底。
 */
import { useState } from 'react'
import { todayBizDate } from '../domain/date'

const TZ = 'Asia/Shanghai'
const WEEKDAYS = ['一', '二', '三', '四', '五', '六', '日'] // 周一起（国内惯例）

function pad(n: number): string {
  return String(n).padStart(2, '0')
}

function fmtYmd(y: number, m: number, d: number): string {
  return `${y}-${pad(m)}-${pad(d)}`
}

/** 按本地日历日偏移（跳过 DST 影响：取正午做基准） */
function shiftDay(iso: string, days: number): string {
  const [y, m, d] = iso.split('-').map(Number)
  const dt = new Date(y, m - 1, d, 12)
  dt.setDate(dt.getDate() + days)
  return fmtYmd(dt.getFullYear(), dt.getMonth() + 1, dt.getDate())
}

function displayLabel(value: string): string {
  const today = todayBizDate(TZ)
  if (value === today) return '今天'
  if (value === shiftDay(today, -1)) return '昨天'
  const mm = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value)
  if (!mm) return value
  const y = Number(mm[1])
  const m = Number(mm[2])
  const d = Number(mm[3])
  const currentYear = Number(today.slice(0, 4))
  return y === currentYear ? `${m}月${d}日` : `${y}年${m}月${d}日`
}

interface DateFieldProps {
  value: string
  onChange: (v: string) => void
  testId?: string
  className?: string
}

export default function DateField({ value, onChange, testId, className = '' }: DateFieldProps) {
  const [open, setOpen] = useState(false)
  const [view, setView] = useState({ y: 0, m: 1 })

  const openSheet = () => {
    const mm = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value)
    if (mm) {
      setView({ y: Number(mm[1]), m: Number(mm[2]) })
    } else {
      const today = todayBizDate(TZ)
      setView({ y: Number(today.slice(0, 4)), m: Number(today.slice(5, 7)) })
    }
    setOpen(true)
  }

  const pick = (v: string) => {
    onChange(v)
    setOpen(false)
  }

  const shiftMonthView = (delta: number) => {
    setView(v => {
      const t = v.m - 1 + delta
      return { y: v.y + Math.floor(t / 12), m: ((t % 12) + 12) % 12 + 1 }
    })
  }

  const today = todayBizDate(TZ)

  // 6×7 格：周一为行首；跨月日用 Date 构造器自动归一，仅标记 inMonth
  const cells = (() => {
    const first = new Date(view.y, view.m - 1, 1, 12)
    const offset = (first.getDay() + 6) % 7
    const daysInMonth = new Date(view.y, view.m, 0).getDate()
    const out: { s: string; inMonth: boolean }[] = []
    for (let i = 0; i < 42; i++) {
      const dayNum = i - offset + 1
      const dt = new Date(view.y, view.m - 1, dayNum, 12)
      out.push({
        s: fmtYmd(dt.getFullYear(), dt.getMonth() + 1, dt.getDate()),
        inMonth: dayNum >= 1 && dayNum <= daysInMonth,
      })
    }
    return out
  })()

  return (
    <div className={className}>
      <button
        type="button"
        onClick={openSheet}
        data-testid={testId}
        data-date={value}
        aria-label="选择日期"
        className="flex items-center gap-1.5 px-3 py-2 rounded-full text-xs active:opacity-60"
        style={{ background: 'var(--glass)', border: '1px solid var(--glass-border)', color: 'var(--ink2)' }}
      >
        <span aria-hidden>📅</span>
        <span>{displayLabel(value)}</span>
      </button>

      {open && (
        <>
          <div className="fixed inset-0 z-[60]" onClick={() => setOpen(false)} data-testid="date-mask" aria-hidden />
          <div
            className="glass-card fixed left-1/2 top-1/2 z-[70] w-[300px] max-w-[92vw] -translate-x-1/2 -translate-y-1/2 p-3"
            style={{ boxShadow: 'var(--card-shadow)' }}
            role="dialog"
            aria-label="选择日期"
            data-testid="date-sheet"
          >
            <div className="mb-2 text-center text-[15px] font-semibold" style={{ color: 'var(--ink)' }}>
              选择日期
            </div>

            <div className="mb-2 flex items-center justify-between">
              <button
                type="button"
                onClick={() => shiftMonthView(-1)}
                aria-label="上个月"
                className="flex h-8 w-8 items-center justify-center rounded-full active:opacity-60"
                style={{ color: 'var(--ink)' }}
              >
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round"><polyline points="15 18 9 12 15 6" /></svg>
              </button>
              <span className="text-[14px] font-semibold" style={{ color: 'var(--ink)' }}>
                {view.y}年{view.m}月
              </span>
              <button
                type="button"
                onClick={() => shiftMonthView(1)}
                aria-label="下个月"
                className="flex h-8 w-8 items-center justify-center rounded-full active:opacity-60"
                style={{ color: 'var(--ink)' }}
              >
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round"><polyline points="9 18 15 12 9 6" /></svg>
              </button>
            </div>

            <div className="mb-1 grid grid-cols-7 text-center text-[11px]" style={{ color: 'var(--ink3)' }}>
              {WEEKDAYS.map(w => (
                <span key={w}>{w}</span>
              ))}
            </div>

            <div className="grid grid-cols-7 gap-y-1 text-center">
              {cells.map(c => {
                const isSel = c.s === value
                const isToday = c.s === today
                return (
                  <button
                    key={c.s}
                    type="button"
                    disabled={!c.inMonth}
                    data-testid={`date-cell-${c.s}`}
                    data-today={isToday || undefined}
                    aria-pressed={isSel}
                    onClick={() => pick(c.s)}
                    className="mx-auto flex h-9 w-9 items-center justify-center rounded-full text-[13px]"
                    style={{
                      background: isSel ? 'var(--accent)' : 'transparent',
                      color: isSel ? 'white' : c.inMonth ? 'var(--ink)' : 'var(--ink3)',
                      opacity: c.inMonth ? 1 : 0.35,
                      border: isToday && !isSel ? '1px solid var(--accent)' : '1px solid transparent',
                    }}
                  >
                    {Number(c.s.slice(8))}
                  </button>
                )
              })}
            </div>

            <div className="mt-3 flex items-center justify-between">
              <button
                type="button"
                onClick={() => pick(today)}
                data-testid="date-today"
                className="rounded-full px-3 py-1.5 text-xs font-medium active:opacity-60"
                style={{ color: 'var(--accent)' }}
              >
                回今天
              </button>
              <button
                type="button"
                onClick={() => setOpen(false)}
                data-testid="date-cancel"
                className="rounded-full px-3 py-1.5 text-xs active:opacity-60"
                style={{ color: 'var(--ink2)' }}
              >
                取消
              </button>
            </div>
          </div>
        </>
      )}
    </div>
  )
}
