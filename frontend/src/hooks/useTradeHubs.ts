import { useQuery } from '@tanstack/react-query'

import { hubsApi } from '../api/client'

// The hub list is static (the four NPC trade hubs), so one fetch per app load.
export function useTradeHubs() {
  return useQuery({
    queryKey: ['hubs', 'list'],
    queryFn: hubsApi.list,
    staleTime: Infinity,
    refetchOnWindowFocus: false,
  })
}
