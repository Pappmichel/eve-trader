import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'

import { useAction } from './useAction'

export interface RoleCharacter {
  role_key: string
  character_id: number
  character_name: string
}

// List + remove for this tool's registered owners. SSO start lives only on
// the Characters page (docs/ESI_ACCESS_PLAN.md Phase 9) — this hook must
// not open a second login path into the app.
export function useRoleCharacters(
  queryKey: string[],
  fetchFn: () => Promise<RoleCharacter[]>,
  removeFn: (roleKey: string) => Promise<unknown>,
) {
  const { data } = useQuery({ queryKey, queryFn: fetchFn })
  const characters = data ?? []

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
    removeCharacter: removeCharacterAt,
    isRemoving,
  }
}
