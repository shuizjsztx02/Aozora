import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, fireEvent } from '@testing-library/react'
import { MemoryRouter, Routes, Route, useNavigate } from 'react-router'
import { ScrollToTop } from '../../app/Router'

function Nav() {
  const navigate = useNavigate()
  return <button onClick={() => navigate('/b')}>go</button>
}

describe('ScrollToTop（第八轮 #1）', () => {
  beforeEach(() => {
    window.scrollTo = vi.fn()
  })

  it('挂载即复位滚动位置', () => {
    render(
      <MemoryRouter initialEntries={['/a']}>
        <ScrollToTop />
        <Routes>
          <Route path="/a" element={<div>A</div>} />
          <Route path="/b" element={<div>B</div>} />
        </Routes>
      </MemoryRouter>
    )
    expect(window.scrollTo).toHaveBeenCalledWith(0, 0)
  })

  it('路由切换时再次复位', () => {
    const { getByText } = render(
      <MemoryRouter initialEntries={['/a']}>
        <ScrollToTop />
        <Routes>
          <Route path="/a" element={<Nav />} />
          <Route path="/b" element={<div>B</div>} />
        </Routes>
      </MemoryRouter>
    )
    ;(window.scrollTo as ReturnType<typeof vi.fn>).mockClear()
    // fireEvent 走 act()，确保 pathname 变化后的 passive effect 已 flush
    fireEvent.click(getByText('go'))
    expect(window.scrollTo).toHaveBeenCalledWith(0, 0)
  })
})
