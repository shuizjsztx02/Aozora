import { describe, it, expect, beforeEach } from 'vitest'
import { render } from '@testing-library/react'
import { SkyCanvas } from '../../design/SkyCanvas'

describe('SkyCanvas', () => {
  beforeEach(() => {
    // Reset localStorage and classes
    try { localStorage.clear() } catch { /* ignore */ }
    document.documentElement.classList.remove('low-perf')
  })

  it('renders with sky-fixed class', () => {
    const { container } = render(<SkyCanvas />)
    const el = container.querySelector('.sky-fixed')
    expect(el).toBeTruthy()
  })

  it('has aria-hidden=true (decoration only)', () => {
    const { container } = render(<SkyCanvas />)
    const el = container.querySelector('.sky-fixed') as HTMLElement
    expect(el?.getAttribute('aria-hidden')).toBe('true')
  })

  it('renders no decorations in low-performance mode', () => {
    try { localStorage.setItem('aozora-low-perf', '1') } catch { /* ignore */ }
    const { container } = render(<SkyCanvas />)
    const el = container.querySelector('.sky-fixed') as HTMLElement
    expect(el?.getAttribute('data-low-perf')).toBe('true')
    // No animated children
    expect(el?.children.length).toBe(0)
  })
})
