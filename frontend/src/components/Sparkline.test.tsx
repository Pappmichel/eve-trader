import { render, screen } from '@testing-library/react'
import { MantineProvider } from '@mantine/core'
import { describe, expect, it } from 'vitest'
import { Sparkline } from './Sparkline'
import { COLORS } from '../theme'

const renderIt = (values: number[]) => render(<MantineProvider><Sparkline values={values} /></MantineProvider>)

describe('Sparkline', () => {
  it('is accent colored when the series ends higher than it starts', () => {
    renderIt([1, 3, 2, 4])
    const svg = screen.getByTestId('sparkline')
    expect(svg.getAttribute('data-direction')).toBe('up')
    expect(svg.querySelector('polyline')?.getAttribute('stroke')).toBe(COLORS.accent)
  })

  it('is danger colored when the series ends lower', () => {
    renderIt([4, 3, 5, 2])
    const svg = screen.getByTestId('sparkline')
    expect(svg.getAttribute('data-direction')).toBe('down')
    expect(svg.querySelector('polyline')?.getAttribute('stroke')).toBe(COLORS.danger)
  })

  it('is dim for a flat series and draws one point per value', () => {
    renderIt([2, 2, 2])
    const svg = screen.getByTestId('sparkline')
    expect(svg.querySelector('polyline')?.getAttribute('stroke')).toBe(COLORS.textDim)
    expect(svg.querySelector('polyline')?.getAttribute('points')?.split(' ')).toHaveLength(3)
  })

  it('renders a dash for fewer than two points', () => {
    renderIt([5])
    expect(screen.queryByTestId('sparkline')).toBeNull()
    expect(screen.getByText('–')).toBeTruthy()
  })
})
