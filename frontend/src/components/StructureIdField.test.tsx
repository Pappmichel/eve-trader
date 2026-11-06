import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MantineProvider } from '@mantine/core'
import { describe, expect, it, vi } from 'vitest'
import { StructureIdField } from './StructureIdField'

function renderField(
  value: number | null,
  structureNames: Record<string, string | null> = {},
  onChange = vi.fn(),
) {
  render(
    <MantineProvider>
      <StructureIdField
        label="Structure ID"
        value={value}
        onChange={onChange}
        structureNames={structureNames}
      />
    </MantineProvider>,
  )
  return onChange
}

describe('StructureIdField', () => {
  it('defaults to the picker, not the numeric ID input', () => {
    renderField(null, { '1020000000001': 'C-J' })
    expect(screen.getByRole('combobox')).toBeTruthy()
    expect(screen.queryByRole('textbox')).toBeNull()
    expect(screen.getByText("Don't see it? Enter ID manually")).toBeTruthy()
  })

  it('defaults to the picker even when the current value is unknown', () => {
    renderField(999, { '1020000000001': 'C-J' })
    expect(screen.getByRole('combobox')).toBeTruthy()
    expect(screen.getByText("Don't see it? Enter ID manually")).toBeTruthy()
  })

  it('shows a saved structure that has no name instead of an empty field', () => {
    renderField(999, { '1020000000001': 'C-J' })
    expect((screen.getByRole('combobox') as HTMLInputElement).value).toBe('999 (not resolved)')
  })

  it('shows the name of a known structure', () => {
    renderField(1020000000001, { '1020000000001': 'C-J' })
    expect((screen.getByRole('combobox') as HTMLInputElement).value).toBe('C-J (1020000000001)')
  })

  it('shows "No known structures" when the known list is empty', async () => {
    renderField(null, {})
    await userEvent.click(screen.getByRole('combobox'))
    expect(await screen.findByText('No known structures')).toBeTruthy()
  })

  it('toggles to numeric entry and back with LocationPicker wording', async () => {
    renderField(null, { '1020000000001': 'C-J' })
    await userEvent.click(screen.getByText("Don't see it? Enter ID manually"))
    expect(screen.getByRole('textbox')).toBeTruthy()
    expect(screen.getByText('Search instead')).toBeTruthy()
    await userEvent.click(screen.getByText('Search instead'))
    expect(screen.getByRole('combobox')).toBeTruthy()
  })
})
