import type { UserStatus } from '../api/types'
import { cn } from '../lib/cn'

const STATUS_META: Record<string, { label: string; className: string }> = {
  guest: { label: '访客', className: 'bg-gray-100 text-gray-700' },
  active: { label: '正常', className: 'bg-green-100 text-green-700' },
  // frozen = 用户申请删除后的 7 天宽限期（不是后台冻结，见架构 §8.1）。
  frozen: { label: '待注销', className: 'bg-amber-100 text-amber-700' },
  deactivated: { label: '已停用', className: 'bg-gray-200 text-gray-600' },
  deleted: { label: '已删除', className: 'bg-red-100 text-red-700' },
}

export const USER_STATUS_OPTIONS: { value: UserStatus; label: string }[] = [
  { value: 'guest', label: '访客' },
  { value: 'active', label: '正常' },
  { value: 'frozen', label: '待注销' },
  { value: 'deactivated', label: '已停用' },
  { value: 'deleted', label: '已删除' },
]

export function StatusBadge({ status }: { status: string }) {
  const meta = STATUS_META[status] ?? {
    label: status,
    className: 'bg-gray-100 text-gray-700',
  }
  return (
    <span
      className={cn('inline-flex rounded px-1.5 py-0.5 text-xs font-medium', meta.className)}
    >
      {meta.label}
    </span>
  )
}
