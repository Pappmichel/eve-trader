import { render, screen } from '@testing-library/react'
import { MemoryRouter, Navigate, Route, Routes } from 'react-router-dom'
import { describe, expect, it } from 'vitest'

// Mirrors the route table in App.tsx: /characters must keep working as a
// redirect into the Character Management hub (old bookmarks, docs links).
describe('/characters redirect', () => {
  it('redirects to /character-management/characters', () => {
    render(
      <MemoryRouter initialEntries={['/characters']}>
        <Routes>
          <Route path="/character-management/characters" element={<div>characters page</div>} />
          <Route path="/characters" element={<Navigate to="/character-management/characters" replace />} />
        </Routes>
      </MemoryRouter>,
    )
    expect(screen.getByText('characters page')).toBeInTheDocument()
  })
})
