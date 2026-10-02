import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MantineProvider } from '@mantine/core'

import { JobProgress, jobProgressPercent } from './JobProgress'

describe('jobProgressPercent', () => {
  it('derives a rounded percentage from batch / total_batches', () => {
    expect(jobProgressPercent({ batch: 1, total_batches: 3 })).toBe(33)
    expect(jobProgressPercent({ batch: 3, total_batches: 3 })).toBe(100)
  })
  it('is null without batch info', () => {
    expect(jobProgressPercent(null)).toBeNull()
    expect(jobProgressPercent({ message: 'Working' })).toBeNull()
    expect(jobProgressPercent({ batch: 2 })).toBeNull()
  })
  it('clamps to 0-100', () => {
    expect(jobProgressPercent({ batch: 9, total_batches: 3 })).toBe(100)
  })
})

describe('JobProgress', () => {
  it('shows the label and a determinate bar when batches are known', () => {
    render(<MantineProvider><JobProgress label="Batch 2/4" progress={{ batch: 2, total_batches: 4 }} /></MantineProvider>)
    expect(screen.getByText('Batch 2/4')).toBeInTheDocument()
    expect(screen.getByLabelText('Job 50% done')).toBeInTheDocument()
  })
  it('shows an indeterminate bar otherwise', () => {
    render(<MantineProvider><JobProgress label="Running…" /></MantineProvider>)
    expect(screen.getByLabelText('Job in progress')).toBeInTheDocument()
  })
})
