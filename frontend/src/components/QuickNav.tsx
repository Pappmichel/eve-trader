import { useNavigate } from 'react-router-dom'
import { Spotlight, type SpotlightActionData } from '@mantine/spotlight'
import { IconSearch } from '@tabler/icons-react'

// GitHub issue #79: the only way to navigate was each tool's own sidebar -
// no fast, cross-tool way to jump from e.g. "Trading > Shortlist" straight
// to "Production > Blueprints" without going back through Landing and
// re-navigating a different sidebar. One flat, static list of every real
// route across all three tools (kept in sync with App.tsx's own <Routes> by
// hand - there's no dynamic route registry to derive this from) grouped by
// tool, filtered by Spotlight's own fuzzy search as you type. Dynamic
// per-entity routes (a specific doctrine/fitting detail page) aren't listed
// here - there's no fixed id to link to and the Doctrines/Contracts list
// pages already link into them.
const ACTIONS: SpotlightActionData[] = [
  { id: 'home', label: 'Tools (Landing)', description: 'Back to the tool picker', onClick: () => {}, keywords: ['home', 'landing'] },
  { id: 'portfolio', label: 'Portfolio', description: 'Combined Trading + Production overview', onClick: () => {} },
  { id: 'admin', label: 'Admin', description: 'Cross-tenant superadmin tools', onClick: () => {} },

  { id: 'trading', label: 'Trading — Shortlist', description: 'Trading', onClick: () => {} },
  { id: 'trading-candidates', label: 'Trading — Candidate Universe', description: 'Trading', onClick: () => {} },
  { id: 'trading-new-candidates', label: 'Trading — New Candidates', description: 'Trading', onClick: () => {} },
  { id: 'trading-history', label: 'Trading — Price History', description: 'Trading', onClick: () => {} },
  { id: 'trading-trades', label: 'Trading — Realized Trades', description: 'Trading', onClick: () => {} },
  { id: 'trading-unlisted-stock', label: 'Trading — Unlisted Stock', description: 'Trading', onClick: () => {} },
  { id: 'trading-undercut', label: 'Trading — Undercut Check', description: 'Trading', onClick: () => {} },
  { id: 'trading-settings', label: 'Trading — Settings', description: 'Trading', onClick: () => {} },

  { id: 'production', label: 'Production — Stock Targets', description: 'Production', onClick: () => {} },
  { id: 'production-market', label: 'Production — Market Status', description: 'Production', onClick: () => {} },
  { id: 'production-jobs', label: 'Production — Industry Jobs', description: 'Production', onClick: () => {} },
  { id: 'production-slots', label: 'Production — Character Slots', description: 'Production', onClick: () => {} },
  { id: 'production-buy', label: 'Production — Buy List', description: 'Production', onClick: () => {} },
  { id: 'production-build', label: 'Production — Build List', description: 'Production', onClick: () => {} },
  { id: 'production-asset-plan', label: 'Production — Build List (Asset-Optimized)', description: 'Production', onClick: () => {} },
  { id: 'production-logistics', label: 'Production — Logistics', description: 'Production', onClick: () => {} },
  { id: 'production-invention', label: 'Production — Invention', description: 'Production', onClick: () => {} },
  { id: 'production-blueprints', label: 'Production — Blueprints', description: 'Production', onClick: () => {} },
  { id: 'production-unlisted-stock', label: 'Production — Unlisted Stock', description: 'Production', onClick: () => {} },
  { id: 'production-build-candidates', label: 'Production — Build Candidates', description: 'Production', onClick: () => {} },
  { id: 'production-margin', label: 'Production — Margin', description: 'Production', onClick: () => {} },
  { id: 'production-material-tree', label: 'Production — Material Tree', description: 'Production', onClick: () => {} },
  { id: 'production-asset-search', label: 'Production — Asset Search', description: 'Production', onClick: () => {} },
  { id: 'production-settings', label: 'Production — Settings', description: 'Production', onClick: () => {} },

  { id: 'doctrine', label: 'Doctrine — Doctrines', description: 'Doctrine', onClick: () => {} },
  { id: 'doctrine-contracts', label: 'Doctrine — Contracts', description: 'Doctrine', onClick: () => {} },
  { id: 'doctrine-contracts-history', label: 'Doctrine — Contract History', description: 'Doctrine', onClick: () => {} },
  { id: 'doctrine-stockpile', label: 'Doctrine — Stockpile', description: 'Doctrine', onClick: () => {} },
  { id: 'doctrine-shopping-list', label: 'Doctrine — Shopping List', description: 'Doctrine', onClick: () => {} },
  { id: 'doctrine-settings', label: 'Doctrine — Settings', description: 'Doctrine', onClick: () => {} },
]

// id -> real path, kept as a separate map (rather than baking navigate()
// calls directly into ACTIONS above) so ACTIONS can stay a plain,
// component-free data array - useNavigate() is only available inside a
// Router, so the actual onClick wiring happens once, here, at render time.
const PATHS: Record<string, string> = {
  home: '/', portfolio: '/portfolio', admin: '/admin',
  trading: '/trading', 'trading-candidates': '/trading/candidates', 'trading-new-candidates': '/trading/new-candidates',
  'trading-history': '/trading/history', 'trading-trades': '/trading/trades', 'trading-unlisted-stock': '/trading/unlisted-stock',
  'trading-undercut': '/trading/undercut', 'trading-settings': '/trading/settings',
  production: '/production', 'production-market': '/production/market', 'production-jobs': '/production/jobs',
  'production-slots': '/production/slots', 'production-buy': '/production/buy', 'production-build': '/production/build',
  'production-asset-plan': '/production/asset-plan', 'production-logistics': '/production/logistics',
  'production-invention': '/production/invention', 'production-blueprints': '/production/blueprints',
  'production-unlisted-stock': '/production/unlisted-stock', 'production-build-candidates': '/production/build-candidates',
  'production-margin': '/production/margin', 'production-material-tree': '/production/material-tree',
  'production-asset-search': '/production/asset-search', 'production-settings': '/production/settings',
  doctrine: '/doctrine', 'doctrine-contracts': '/doctrine/contracts', 'doctrine-contracts-history': '/doctrine/contracts/history',
  'doctrine-stockpile': '/doctrine/stockpile', 'doctrine-shopping-list': '/doctrine/shopping-list',
  'doctrine-settings': '/doctrine/settings',
}

export function QuickNav() {
  const navigate = useNavigate()
  const actions = ACTIONS.map((a) => ({ ...a, onClick: () => navigate(PATHS[a.id]) }))

  return (
    <Spotlight
      actions={actions}
      shortcut="mod + K"
      searchProps={{ leftSection: <IconSearch size={16} />, placeholder: 'Jump to a page...' }}
      nothingFound="No matching page."
    />
  )
}
