// Plain-language "what does logging in as this role actually read" text,
// shown in the confirm-before-login modal (useRoleCharacters.ts) and on
// Landing.tsx's own "gate" login button - one shared source so the wording
// can't drift between the two. Derived from the real ESI scopes each
// role_prefix requests (eve_trader/config.py's OAuthConfig.scopes for
// buyer/seller, production/esi_sync.py's PRODUCTION_SCOPES, doctrine/
// esi_sync.py's DOCTRINE_SCOPES/DOCTRINE_ASSET_SCOPES) - keep this in sync
// if a role's scopes ever change.
import { List, Stack, Text } from '@mantine/core'
import { modals } from '@mantine/modals'

export interface RoleAccessInfo {
  title: string
  bullets: string[]
}

export const ROLE_ACCESS_DESCRIPTIONS: Record<string, RoleAccessInfo> = {
  buyer: {
    title: 'Trading — Buyer Character',
    bullets: [
      'Your market orders, to track buy-side order fills',
      "C-J's sell order book, to compare against Jita prices",
      'Your wallet transactions, to reconcile realized trades',
      "Your personal assets, to know what's already been bought",
    ],
  },
  seller: {
    title: 'Trading — Seller Character',
    bullets: [
      'Your market orders, to track sell-side fills and realized profit',
      "C-J's sell order book, to compare against Jita prices",
      'Your wallet transactions, to reconcile realized trades',
      'Your personal assets, to find unlisted stock ready to sell',
    ],
  },
  producer: {
    title: 'Production — Producer Character',
    bullets: [
      "Your (and your corporation's) assets - current stock of materials and components",
      "Your (and your corporation's) blueprints - ME/TE levels, owned BPOs vs. BPCs",
      "Your (and your corporation's) industry jobs - in-progress manufacturing and reactions",
      "Your (and your corporation's) market orders - sell orders for finished production goods",
      'Your skills, used to compute available manufacturing job slots',
      'Names of structures you (and your corporation) have access to',
    ],
  },
  doctrine: {
    title: 'Doctrine — Contract Character',
    bullets: [
      "Your (and your corporation's) item-exchange contracts, validated against the fleet doctrine's required fits",
      'Names of structures you have access to',
    ],
  },
  'doctrine-assets': {
    title: 'Doctrine — Stockpile Character',
    bullets: [
      "Your (and your corporation's) asset hangars, tracked against doctrine stockpile requirements",
    ],
  },
  trader: {
    title: 'Station Trading — Trader Character',
    bullets: [
      "Your market orders (buy and sell) at Jita, to check if you've been undercut on either side",
      'Your skills, to show your available concurrent order slots',
    ],
  },
  gate: {
    title: 'Account Login',
    bullets: [
      'Only your EVE Online character identity (name and character ID) - no game data (assets, orders, wallet, contracts, industry jobs, etc.) is ever read for this login',
    ],
  },
}

// Shared modal body/labels so the confirm-before-login flow looks identical
// everywhere it appears (useRoleCharacters.ts for buyer/seller/producer/
// doctrine/doctrine-assets, Landing.tsx for gate).
export function openRoleAccessConfirmModal(rolePrefix: string, onConfirm: () => void) {
  const info = ROLE_ACCESS_DESCRIPTIONS[rolePrefix]
  modals.openConfirmModal({
    title: info?.title ?? 'Confirm data access',
    children: (
      <Stack gap="xs">
        <Text size="sm">Logging in here will let EVE Trader read:</Text>
        <List size="sm" spacing={4}>
          {(info?.bullets ?? []).map((bullet) => <List.Item key={bullet}>{bullet}</List.Item>)}
        </List>
        <Text size="xs" c="dimmed">You'll only see this once per role - shown again only if this changes.</Text>
      </Stack>
    ),
    labels: { confirm: 'Continue to EVE Online Login', cancel: 'Cancel' },
    onConfirm,
  })
}
