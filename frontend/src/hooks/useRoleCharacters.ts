import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'

import { authApi } from '../api/client'
import { useAction } from './useAction'

export interface RoleCharacter {
  role_key: string
  character_id: number
  character_name: string
}

// GitHub issue #69 (found in a full-codebase audit 2026-08-21): the
// "fetch a role's registered characters, log in another via redirect-based
// EVE SSO, remove one with its own per-row loading state" pattern was
// implemented three separate times (TradingLayout.tsx, ProductionLayout.tsx,
// DoctrineLayout.tsx) with only minor prop differences - and only two of
// the three copies got the per-row-loading-state fix (see GitHub issue #59)
// before this existed, which is exactly the drift duplication invites.
// Shares the stateful logic only, not rendering - each page still owns its
// own JSX/styling (Trading uses a Badge + text Button, Doctrine uses an
// ActionIcon + trash icon, Production integrates into a denser sidebar
// section), since those three call sites genuinely want different layouts;
// what should never drift again is the mutation/pending-state wiring
// itself.
export function useRoleCharacters(
  queryKey: string[],
  fetchFn: () => Promise<RoleCharacter[]>,
  removeFn: (roleKey: string) => Promise<unknown>,
  ssoRolePrefix: string,
) {
  const { data } = useQuery({ queryKey, queryFn: fetchFn })
  const characters = data ?? []

  // Same redirect-based EVE SSO flow every one of the three original call
  // sites used - navigates the whole page to EVE SSO and back via
  // api/routers/auth.py's /callback route, rather than the old server-side
  // webbrowser.open() + blocking-local-HTTP-server flow (which only worked
  // when the backend and browser happened to be on the same machine, and
  // tied up the request thread for minutes - confirmed real bug on a real
  // deployment).
  const addCharacter = useAction('Login', async () => {
    const { url } = await authApi.start(ssoRolePrefix)
    window.location.href = url
  })

  const removeCharacter = useAction('Remove Character', removeFn, [queryKey])
  // One shared mutation instance is reused across every character's Remove
  // button - without tracking which row is actually pending, clicking
  // Remove for one character puts *every* character's button into the
  // loading/disabled state (GitHub issue #59, confirmed real bug).
  const [pendingRoleKey, setPendingRoleKey] = useState<string | null>(null)

  const removeCharacterAt = (roleKey: string) => {
    setPendingRoleKey(roleKey)
    removeCharacter.mutate(roleKey)
  }
  const isRemoving = (roleKey: string) => removeCharacter.isPending && pendingRoleKey === roleKey

  return {
    characters,
    addCharacter,
    removeCharacter: removeCharacterAt,
    isRemoving,
  }
}
