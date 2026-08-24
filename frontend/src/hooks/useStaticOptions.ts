import { useQuery } from '@tanstack/react-query'
import { productionApi } from '../api/client'

// One fetch per app load (staleTime: Infinity) - same pattern as App.tsx's
// SdeFreshnessChecker. Filtered client-side on every keystroke by
// SearchableSelect, never re-queried per keystroke.
export function useItemNameOptions() {
  return useQuery({
    queryKey: ['production', 'sde', 'item-names'],
    queryFn: productionApi.itemNameOptions,
    staleTime: Infinity,
    refetchOnWindowFocus: false,
  })
}

export function useSolarSystemOptions() {
  return useQuery({
    queryKey: ['production', 'systems', 'all'],
    queryFn: productionApi.allSolarSystems,
    staleTime: Infinity,
    refetchOnWindowFocus: false,
  })
}

// Per-tenant, not static SDE data - still safe to cache indefinitely within
// one page session (a reload re-fetches). Reuses the exact query key
// ['production', 'structure-names'] that Logistics.tsx/ProductionSettings.tsx
// already invalidate after a resolve/sync action, so those existing
// invalidations keep this picker's data fresh too, with no extra wiring.
export function useStructureNameOptions() {
  return useQuery({
    queryKey: ['production', 'structure-names'],
    queryFn: productionApi.structureNames,
    staleTime: Infinity,
    refetchOnWindowFocus: false,
  })
}
