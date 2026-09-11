/**
 * TimeField — 受控「时:分」步进选择（第十四轮 T14-5 / REQ-43）。
 *
 * 样式逐值照搬 ReminderSettingsPage 的 ▲▼ 两列滚轮（w-16 按钮 /
 * text-3xl money-num 数值 / 时·分说明），改造成受控组件：宿主给
 * {hour, minute}，▲ 前进 / ▼ 后退，越界回绕（时 23→0、分 59→0，双向）。
 * 不进弹层，直接内联进表单。ReminderSettingsPage 本轮不重构复用（最小改动）。
 */

interface TimeFieldProps {
  hour: number
  minute: number
  onChange: (hour: number, minute: number) => void
  /** 宿主测试钩子前缀；内部按钮 = {testId}-hour-up / -hour-down / -minute-up / -minute-down */
  testId?: string
}

const BTN_STYLE = { background: 'var(--glass)', color: 'var(--ink)' } as const

export default function TimeField({ hour, minute, onChange, testId }: TimeFieldProps) {
  const id = (name: string) => (testId ? `${testId}-${name}` : undefined)
  return (
    <div className="flex items-center gap-2">
      {/* 小时 */}
      <div className="flex flex-col items-center">
        <button
          type="button"
          onClick={() => onChange((hour + 1) % 24, minute)}
          className="w-16 h-10 rounded-lg flex items-center justify-center text-lg"
          style={BTN_STYLE}
          data-testid={id('hour-up')}
          aria-label="加1小时"
        >
          ▲
        </button>
        <div className="text-3xl font-light py-2 money-num" style={{ color: 'var(--ink)' }} data-testid={id('hour')}>
          {String(hour).padStart(2, '0')}
        </div>
        <button
          type="button"
          onClick={() => onChange((hour - 1 + 24) % 24, minute)}
          className="w-16 h-10 rounded-lg flex items-center justify-center text-lg"
          style={BTN_STYLE}
          data-testid={id('hour-down')}
          aria-label="减1小时"
        >
          ▼
        </button>
        <div className="text-xs text-ink3 mt-1">时</div>
      </div>

      <div className="text-3xl font-light" style={{ color: 'var(--ink)' }}>:</div>

      {/* 分钟 */}
      <div className="flex flex-col items-center">
        <button
          type="button"
          onClick={() => onChange(hour, (minute + 1) % 60)}
          className="w-16 h-10 rounded-lg flex items-center justify-center text-lg"
          style={BTN_STYLE}
          data-testid={id('minute-up')}
          aria-label="加1分钟"
        >
          ▲
        </button>
        <div className="text-3xl font-light py-2 money-num" style={{ color: 'var(--ink)' }} data-testid={id('minute')}>
          {String(minute).padStart(2, '0')}
        </div>
        <button
          type="button"
          onClick={() => onChange(hour, (minute - 1 + 60) % 60)}
          className="w-16 h-10 rounded-lg flex items-center justify-center text-lg"
          style={BTN_STYLE}
          data-testid={id('minute-down')}
          aria-label="减1分钟"
        >
          ▼
        </button>
        <div className="text-xs text-ink3 mt-1">分</div>
      </div>
    </div>
  )
}
