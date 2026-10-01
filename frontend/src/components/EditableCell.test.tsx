import { describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MantineProvider } from '@mantine/core'

import { EditableNumberCell } from './EditableCell'

function renderCell(onSave = vi.fn()) {
  render(
    <MantineProvider>
      <EditableNumberCell value={5} ariaLabel="Target" isPending={false} onSave={onSave} />
    </MantineProvider>,
  )
  return onSave
}

describe('EditableNumberCell', () => {
  it('shows no save button until the value changes', () => {
    renderCell()
    expect(screen.queryByRole('button', { name: 'Save Target' })).not.toBeInTheDocument()
  })

  it('saves with the save button', async () => {
    const user = userEvent.setup()
    const onSave = renderCell()
    await user.clear(screen.getByLabelText('Target'))
    await user.type(screen.getByLabelText('Target'), '12')
    await user.click(screen.getByRole('button', { name: 'Save Target' }))
    expect(onSave).toHaveBeenCalledWith(12)
  })

  it('saves on Enter and discards on Escape; never saves on blur', async () => {
    const user = userEvent.setup()
    const onSave = renderCell()
    const input = screen.getByLabelText('Target')

    await user.clear(input)
    await user.type(input, '9')
    await user.tab() // blur
    expect(onSave).not.toHaveBeenCalled()

    await user.click(input)
    await user.keyboard('{Escape}')
    expect(input).toHaveValue('5')
    expect(screen.queryByRole('button', { name: 'Save Target' })).not.toBeInTheDocument()

    await user.clear(input)
    await user.type(input, '7{Enter}')
    expect(onSave).toHaveBeenCalledWith(7)
  })
})
