import { useEffect } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { ActionIcon, Button, Tooltip } from '@mantine/core'
import { IconRefresh } from '@tabler/icons-react'

import { updatesApi } from '../api/client'
import {
  DATA_SOURCE_LABELS, DATA_VERSIONS_KEY, recordDataVersions, refreshForNewData, useChangedDataSources,
} from '../dataVersions'

// Polls the data versions every two minutes while the tab is visible and
// offers one click to reload when the scheduler changed something
// (FRONTEND_PLAN.md B.12). No automatic reload: a table the user is reading
// must not jump under them.
const POLL_MS = 120_000

export function NewDataNotice() {
  const queryClient = useQueryClient()
  const { data } = useQuery({
    queryKey: DATA_VERSIONS_KEY,
    queryFn: updatesApi.versions,
    refetchInterval: POLL_MS,
    refetchIntervalInBackground: false,
    refetchOnWindowFocus: true,
    retry: false,
  })
  useEffect(() => {
    if (data) recordDataVersions(data)
  }, [data])
  const changed = useChangedDataSources()
  if (changed.length === 0) return null

  const label = `New data: ${changed.map((k) => DATA_SOURCE_LABELS[k] ?? k).join(', ')}. Click to refresh.`
  const refresh = () => refreshForNewData(queryClient)
  return (
    <Tooltip label={label} multiline w={260}>
      <span>
        <Button visibleFrom="sm" size="xs" variant="light" color="accent" leftSection={<IconRefresh size={14} />}
          onClick={refresh}>
          New data
        </Button>
        <ActionIcon hiddenFrom="sm" size="lg" variant="light" color="accent" aria-label={label} onClick={refresh}>
          <IconRefresh size={18} />
        </ActionIcon>
      </span>
    </Tooltip>
  )
}
