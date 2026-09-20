// Confirm-before-login modal. Characters re-auth fetches a registry-derived
// payload from `/api/characters/access-preview` (`added` highlights kinds
// that are not on any existing token). Identity-only `gate` stays a static
// localStorage dialog on Landing — it requests no game data and has no
// tenant at the moment the dialog is shown.
import { List, Stack, Text } from '@mantine/core'
import { modals } from '@mantine/modals'

export interface AccessPreviewItem {
  key: string
  label: string
  group: number
  added: boolean
}

export interface AccessPreview {
  title: string
  items: AccessPreviewItem[]
}

const GATE_PREVIEW: AccessPreview = {
  title: 'Account Login',
  items: [{
    key: 'gate',
    label: 'Only your EVE Online character identity (name and character ID) - no game data (assets, orders, wallet, contracts, industry jobs, etc.) is ever read for this login',
    group: 0,
    added: false,
  }],
}

export function openGateAccessConfirmModal(onConfirm: () => void) {
  openAccessConfirmModal(GATE_PREVIEW, onConfirm)
}

export function openAccessConfirmModal(preview: AccessPreview, onConfirm: () => void) {
  modals.openConfirmModal({
    title: preview.title || 'Confirm data access',
    children: (
      <Stack gap="xs">
        <Text size="sm">Logging in here will let EVE Trader read:</Text>
        <List size="sm" spacing={4}>
          {preview.items.map((item) => (
            <List.Item key={item.key}>
              <Text span fw={item.added ? 700 : 400} c={item.added ? undefined : 'dimmed'}>
                {item.label}{item.added ? ' (new)' : ''}
              </Text>
            </List.Item>
          ))}
        </List>
      </Stack>
    ),
    labels: { confirm: 'Continue to EVE Online Login', cancel: 'Cancel' },
    onConfirm,
  })
}
