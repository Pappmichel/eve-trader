import { useQuery } from '@tanstack/react-query'

export interface RoleCharacter {
  role_key: string
  character_id: number
  character_name: string
}

// List this tool's registered owners. SSO start and token removal live only
// on the Characters page (docs/ESI_ACCESS_PLAN.md Phase 9) — this hook must
// not open a second login path, or a per-prefix delete of a merged key.
export function useRoleCharacters(
  queryKey: string[],
  fetchFn: () => Promise<RoleCharacter[]>,
) {
  const { data } = useQuery({ queryKey, queryFn: fetchFn })
  return { characters: data ?? [] }
}
