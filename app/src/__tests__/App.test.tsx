import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import App from '../App'

describe('Test framework readiness', () => {
  it('vitest and jest-dom matchers are available', () => {
    expect(document.body).toBeInTheDocument()
  })
})

describe('App', () => {
  it('renders the app shell with sky canvas', () => {
    render(<App />)
    expect(screen.getByTestId('sky-canvas')).toBeInTheDocument()
  })
})
