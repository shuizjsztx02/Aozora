/**
 * T-M8-09 导出页。
 *
 * 三种范围 × 两种内容（用户决策 14）：
 * - 范围：全库 / 按用户 / 按时间与关键词（当前筛选口径）；
 * - 内容：明文（plain）/ 打码（masked，默认）。
 *
 * 点击即导出，**没有二次确认**（用户决策）；服务端流式返回 NDJSON 并写一条
 * `admin_audit_log(action="export")`。超限（100,000 事件）返回 422
 * `export_too_large`，提示缩小范围后分批。
 */

import { useState, type FormEvent, type ReactNode } from 'react'
import { downloadExport, formatBytes, type ExportFormat, type ExportScope } from '../api/export'
import { QUERY_MIN_CHARS } from '../api/admin'
import { useAuth } from '../auth/AuthContext'
import { cn } from '../lib/cn'
import { describeError } from '../lib/errors'
import { beijingInputToIso } from '../lib/time'
import { buttonPrimaryClass, inputClass } from '../lib/ui'

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i

interface ExportForm {
  scope: ExportScope
  format: ExportFormat
  userId: string
  q: string
  timeFrom: string
  timeTo: string
  includeArchived: boolean
}

const DEFAULT_FORM: ExportForm = {
  scope: 'all',
  format: 'masked',
  userId: '',
  q: '',
  timeFrom: '',
  timeTo: '',
  includeArchived: false,
}

export function ExportPage() {
  const { session } = useAuth()
  const [form, setForm] = useState<ExportForm>(DEFAULT_FORM)
  const [hint, setHint] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [progress, setProgress] = useState<number | null>(null)
  const [done, setDone] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  function update<K extends keyof ExportForm>(key: K, value: ExportForm[K]) {
    setForm((prev) => ({ ...prev, [key]: value }))
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setHint(null)
    setDone(null)
    setError(null)

    const userId = form.userId.trim()
    if (form.scope === 'user' && !UUID_RE.test(userId)) {
      setHint('按用户导出需要完整用户 ID（UUID）')
      return
    }
    const q = form.q.trim()
    if (form.scope === 'filtered' && q && q.length < QUERY_MIN_CHARS) {
      setHint(`关键词至少 ${QUERY_MIN_CHARS} 个字符`)
      return
    }
    if (!session) {
      setError('登录已失效，请重新登录')
      return
    }

    setBusy(true)
    setProgress(0)
    try {
      const result = await downloadExport(
        {
          scope: form.scope,
          format: form.format,
          content: 'events',
          ...(form.scope === 'user' ? { user_id: userId } : {}),
          ...(form.scope === 'filtered'
            ? {
                q: q || undefined,
                time_from: beijingInputToIso(form.timeFrom),
                time_to: beijingInputToIso(form.timeTo),
                include_archived: form.includeArchived,
              }
            : {}),
        },
        session.accessToken,
        { onProgress: setProgress },
      )
      setDone(`已下载 ${result.filename}（${formatBytes(result.bytes)}）`)
    } catch (err) {
      setError(describeError(err))
    } finally {
      setBusy(false)
      setProgress(null)
    }
  }

  return (
    <section className="space-y-3">
      <h1 className="text-lg font-semibold text-gray-900">导出</h1>

      <form onSubmit={(event) => void submit(event)} className="space-y-4 rounded-lg border border-gray-200 bg-white p-4">
        <FieldGroup label="范围">
          <div className="flex flex-wrap gap-x-4 gap-y-2 text-sm text-gray-700">
            <Radio
              name="scope"
              value="all"
              checked={form.scope === 'all'}
              onChange={() => update('scope', 'all')}
            >
              全库
            </Radio>
            <Radio
              name="scope"
              value="user"
              checked={form.scope === 'user'}
              onChange={() => update('scope', 'user')}
            >
              按用户
            </Radio>
            <Radio
              name="scope"
              value="filtered"
              checked={form.scope === 'filtered'}
              onChange={() => update('scope', 'filtered')}
            >
              按时间与关键词
            </Radio>
          </div>
        </FieldGroup>

        {form.scope === 'user' ? (
          <FieldGroup label="用户 ID（完整 UUID）">
            <input
              className={inputClass}
              value={form.userId}
              onChange={(event) => update('userId', event.target.value)}
              placeholder="例如 3f2c9a1e-…"
              spellCheck={false}
            />
          </FieldGroup>
        ) : null}

        {form.scope === 'filtered' ? (
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
            <FieldGroup label={`关键词（≥ ${QUERY_MIN_CHARS} 字符，可选）`}>
              <input
                className={inputClass}
                value={form.q}
                onChange={(event) => update('q', event.target.value)}
                placeholder="用户输入或助手回复中的关键词"
              />
            </FieldGroup>
            <FieldGroup label="最后活跃起（北京时间）">
              <input
                type="datetime-local"
                className={inputClass}
                value={form.timeFrom}
                onChange={(event) => update('timeFrom', event.target.value)}
              />
            </FieldGroup>
            <FieldGroup label="最后活跃止（北京时间）">
              <input
                type="datetime-local"
                className={inputClass}
                value={form.timeTo}
                onChange={(event) => update('timeTo', event.target.value)}
              />
            </FieldGroup>
            <label className="flex items-center gap-2 text-sm text-gray-700">
              <input
                type="checkbox"
                checked={form.includeArchived}
                onChange={(event) => update('includeArchived', event.target.checked)}
              />
              包含已归档会话
            </label>
          </div>
        ) : null}

        <FieldGroup label="内容">
          <div className="flex flex-wrap gap-x-4 gap-y-2 text-sm text-gray-700">
            <Radio
              name="format"
              value="masked"
              checked={form.format === 'masked'}
              onChange={() => update('format', 'masked')}
            >
              打码版（手机号 / 邮箱 / 身份证 / 银行卡）
            </Radio>
            <Radio
              name="format"
              value="plain"
              checked={form.format === 'plain'}
              onChange={() => update('format', 'plain')}
            >
              明文
            </Radio>
          </div>
        </FieldGroup>

        <div className="flex flex-wrap items-center gap-3">
          <button type="submit" className={buttonPrimaryClass} disabled={busy}>
            {busy ? '导出中…' : '开始导出'}
          </button>
          {busy && progress !== null ? (
            <span className="text-xs text-gray-500">已接收 {formatBytes(progress)}…</span>
          ) : null}
          {done ? <span className="text-xs text-green-700">{done}</span> : null}
          {hint ? <span className="text-sm text-red-600">{hint}</span> : null}
          {error ? <span className="text-sm text-red-600">{error}</span> : null}
        </div>

        <p className="text-xs text-gray-500">
          导出为流式 JSONL（每行一个事件对象）。单次上限 100,000 事件，超出请缩小时间范围或
          选择单个用户后分批。每次导出都会写一条审计记录；本页不做二次确认。
        </p>
      </form>
    </section>
  )
}

function FieldGroup({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex min-w-0 flex-col gap-1">
      <span className="text-xs font-medium text-gray-600">{label}</span>
      {children}
    </div>
  )
}

function Radio({
  name,
  value,
  checked,
  onChange,
  children,
}: {
  name: string
  value: string
  checked: boolean
  onChange: () => void
  children: ReactNode
}) {
  return (
    <label className={cn('flex items-center gap-2', checked ? 'text-gray-900' : 'text-gray-700')}>
      <input type="radio" name={name} value={value} checked={checked} onChange={onChange} />
      {children}
    </label>
  )
}
