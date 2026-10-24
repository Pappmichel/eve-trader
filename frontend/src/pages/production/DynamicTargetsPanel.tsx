import { Badge, Button, Card, Group, Switch, Text, Title } from '@mantine/core'
import { modals } from '@mantine/modals'
import { useQuery } from '@tanstack/react-query'

import { productionApi } from '../../api/client'
import type { DynamicTargetSettings, StockTarget } from '../../api/types'
import { useAction } from '../../hooks/useAction'
import { DYNAMIC_KEYS, DYNAMIC_SETTINGS_KEY, bulkCandidates } from './dynamicTargetKeys'

export function DynamicTargetsPanel({ targets }: { targets: StockTarget[] }) {
  const { data: settings } = useQuery({ queryKey: DYNAMIC_SETTINGS_KEY, queryFn: productionApi.dynamicTargetSettings })
  const enabled = settings?.dynamic_targets_enabled ?? false

  const saveSettings = useAction('Save Dynamic Targets Setting',
    (s: DynamicTargetSettings) => productionApi.updateDynamicTargetSettings(s), DYNAMIC_KEYS)
  const setAll = useAction('Set All Stock Target Modes',
    (mode: 'fixed' | 'dynamic') => productionApi.setAllStockTargetModes(mode), DYNAMIC_KEYS)
  const recalculate = useAction('Recalculate Dynamic Targets', productionApi.recalculateDynamicTargets, DYNAMIC_KEYS)

  const dynamicRows = targets.filter((t) => t.target_mode === 'dynamic').length
  const { applicable, skipped } = bulkCandidates(targets)

  const confirmAll = (mode: 'fixed' | 'dynamic') => modals.openConfirmModal({
    title: mode === 'dynamic' ? 'Switch all rows to dynamic' : 'Switch all rows to fixed',
    children: (
      <Text size="sm">
        {mode === 'dynamic'
          ? `${applicable} row(s) with a home market target become dynamic (min 0, max = 3x the current value, at least 10; existing min/max are kept). `
            + (skipped > 0 ? `${skipped} row(s) without a home market target are skipped. ` : '')
            + 'Nothing changes for the planners until the switch above is on.'
          : `All ${targets.length} row(s) become fixed again. Min/max stay stored, so switching back restores them.`}
      </Text>
    ),
    labels: { confirm: mode === 'dynamic' ? 'All to dynamic' : 'All to fixed', cancel: 'Cancel' },
    onConfirm: () => setAll.mutate(mode),
  })

  return (
    <Card withBorder>
      <Group justify="space-between" align="flex-start">
        <div>
          <Title order={6} c="dimmed" tt="uppercase" mb="xs">Dynamic Targets</Title>
          <Text size="xs" c="dimmed" maw={620}>
            A dynamic row's home market target follows its own sales (or the market) and its margin, frozen once per day,
            between the row's min and max. Backup and Jita targets stay fixed. Nothing is sold or delisted when a target drops.
          </Text>
        </div>
        <Group>
          <Switch
            label="Dynamic targets"
            aria-label="Dynamic targets on or off"
            checked={enabled}
            disabled={!settings || saveSettings.isPending}
            onChange={(e) => settings && saveSettings.mutate({ ...settings, dynamic_targets_enabled: e.currentTarget.checked })}
          />
          <Badge variant="light" color={enabled ? 'accent' : 'gray'}>{enabled ? 'on' : 'off'}</Badge>
        </Group>
      </Group>
      {!enabled && (
        <Text size="sm" c="dimmed" mt="xs">
          Dynamic targets are off - every row uses its fixed values{dynamicRows > 0 ? ` (${dynamicRows} row(s) are set to dynamic and take effect once this is on)` : ''}.
        </Text>
      )}
      <Group mt="sm">
        <Button size="xs" variant="light" onClick={() => confirmAll('dynamic')} loading={setAll.isPending}
          disabled={applicable === 0}>
          All to dynamic
        </Button>
        <Button size="xs" variant="light" color="gray" onClick={() => confirmAll('fixed')} loading={setAll.isPending}
          disabled={dynamicRows === 0}>
          All to fixed
        </Button>
        <Button size="xs" variant="default" onClick={() => recalculate.mutate()} loading={recalculate.isPending}
          disabled={!enabled || dynamicRows === 0}>
          Recalculate
        </Button>
      </Group>
    </Card>
  )
}
