import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { BottomNav } from '../../components/BottomNav'

function renderWithRouter(ui: React.ReactElement) {
  return render(<MemoryRouter>{ui}</MemoryRouter>)
}

describe('BottomNav', () => {
  it('renders all 4 nav items', () => {
    renderWithRouter(<BottomNav />)
    expect(screen.getByText('首页')).toBeInTheDocument()
    expect(screen.getByText('明细')).toBeInTheDocument()
    expect(screen.getByText('报表')).toBeInTheDocument()
    expect(screen.getByText('我的')).toBeInTheDocument()
  })

  it('has testid', () => {
    renderWithRouter(<BottomNav />)
    expect(screen.getByTestId('bottom-nav')).toBeInTheDocument()
  })
})
