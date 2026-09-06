import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { EmptyState, LoadingState, ErrorState, PendingSyncState } from '../../components/StateViews'

describe('EmptyState', () => {
  it('renders title', () => {
    render(<EmptyState title="暂无数据" />)
    expect(screen.getByText('暂无数据')).toBeInTheDocument()
  })

  it('renders description', () => {
    render(<EmptyState title="空" description="请添加数据" />)
    expect(screen.getByText('请添加数据')).toBeInTheDocument()
  })

  it('renders with testid', () => {
    render(<EmptyState title="空" />)
    expect(screen.getByTestId('empty-state')).toBeInTheDocument()
  })

  it('renders action', () => {
    render(<EmptyState title="空" action={<button>添加</button>} />)
    expect(screen.getByText('添加')).toBeInTheDocument()
  })
})

describe('LoadingState', () => {
  it('renders', () => {
    render(<LoadingState />)
    expect(screen.getByTestId('loading-state')).toBeInTheDocument()
  })
})

describe('ErrorState', () => {
  it('renders error message', () => {
    render(<ErrorState message="网络错误" />)
    expect(screen.getByText('网络错误')).toBeInTheDocument()
  })

  it('renders retry button when onRetry provided', () => {
    render(<ErrorState onRetry={() => {}} />)
    expect(screen.getByText('重试')).toBeInTheDocument()
  })

  it('renders with testid', () => {
    render(<ErrorState />)
    expect(screen.getByTestId('error-state')).toBeInTheDocument()
  })
})

describe('PendingSyncState', () => {
  it('shows pending count', () => {
    render(<PendingSyncState count={3} />)
    expect(screen.getByText('3 条待同步')).toBeInTheDocument()
  })

  it('shows synced when count is 0', () => {
    render(<PendingSyncState count={0} />)
    expect(screen.getByText('已同步')).toBeInTheDocument()
  })

  it('shows error indicator when lastError present', () => {
    render(<PendingSyncState count={1} lastError="timeout" />)
    expect(screen.getByText('· 有误')).toBeInTheDocument()
  })
})
