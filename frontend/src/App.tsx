import { Suspense, lazy, useEffect } from 'react'
import { Routes, Route, useSearchParams, useNavigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { notifications } from '@mantine/notifications'
import { Center, Loader } from '@mantine/core'

import Landing from './pages/Landing'
import { productionApi } from './api/client'
import { ErrorBoundary } from './components/ErrorBoundary'

const Portfolio = lazy(() => import('./pages/Portfolio'))

// Lazy-loaded: Trading and Production are two largely-independent tool
// sections (separate layouts, separate nav) - splitting each page into its
// own chunk means visiting one section never downloads the other's code,
// and the initial load (Landing) stays small instead of shipping all ~20
// pages' worth of JS (recharts, tanstack-table, etc.) up front. Confirmed
// real: the pre-split production build was a single ~1MB bundle, over Vite's
// own 500kB chunk-size warning threshold.
const TradingLayout = lazy(() => import('./pages/trading/TradingLayout'))
const Shortlist = lazy(() => import('./pages/trading/Shortlist'))
const Candidates = lazy(() => import('./pages/trading/Candidates'))
const NewCandidates = lazy(() => import('./pages/trading/NewCandidates'))
const PriceHistory = lazy(() => import('./pages/trading/PriceHistory'))
const RealizedTrades = lazy(() => import('./pages/trading/RealizedTrades'))
const UnlistedStock = lazy(() => import('./pages/trading/UnlistedStock'))
const UndercutCheck = lazy(() => import('./pages/trading/UndercutCheck'))
const TradingSettings = lazy(() => import('./pages/trading/TradingSettings'))

const ProductionLayout = lazy(() => import('./pages/production/ProductionLayout'))
const StockTargets = lazy(() => import('./pages/production/StockTargets'))
const MarketStatus = lazy(() => import('./pages/production/MarketStatus'))
const Jobs = lazy(() => import('./pages/production/Jobs'))
const Slots = lazy(() => import('./pages/production/Slots'))
const BuyList = lazy(() => import('./pages/production/BuyList'))
const BuildList = lazy(() => import('./pages/production/BuildList'))
const AssetPlanList = lazy(() => import('./pages/production/AssetPlanList'))
const Logistics = lazy(() => import('./pages/production/Logistics'))
const Invention = lazy(() => import('./pages/production/Invention'))
const Blueprints = lazy(() => import('./pages/production/Blueprints'))
const ProductionUnlistedStock = lazy(() => import('./pages/production/UnlistedStock'))
const BuildCandidates = lazy(() => import('./pages/production/BuildCandidates'))
const Margin = lazy(() => import('./pages/production/Margin'))
const MaterialTree = lazy(() => import('./pages/production/MaterialTree'))
const AssetSearch = lazy(() => import('./pages/production/AssetSearch'))
const ProductionSettings = lazy(() => import('./pages/production/ProductionSettings'))

const DoctrineLayout = lazy(() => import('./pages/doctrine/DoctrineLayout'))
const Doctrines = lazy(() => import('./pages/doctrine/Doctrines'))
const DoctrineDetail = lazy(() => import('./pages/doctrine/DoctrineDetail'))
const FittingDetail = lazy(() => import('./pages/doctrine/FittingDetail'))
const Contracts = lazy(() => import('./pages/doctrine/Contracts'))
const ContractHistory = lazy(() => import('./pages/doctrine/ContractHistory'))
const Stockpile = lazy(() => import('./pages/doctrine/Stockpile'))
const ShoppingList = lazy(() => import('./pages/doctrine/ShoppingList'))
const DoctrineSettings = lazy(() => import('./pages/doctrine/DoctrineSettings'))

const AdminPage = lazy(() => import('./pages/admin/AdminPage'))

function RouteFallback() {
  return (
    <Center h={200}>
      <Loader color="accent" />
    </Center>
  )
}

function AuthRedirectHandler() {
  const [params, setParams] = useSearchParams()
  const navigate = useNavigate()

  useEffect(() => {
    const auth = params.get('auth')
    const gate = params.get('gate')
    if (auth === 'success') {
      const role = params.get('role')
      const character = params.get('character')
      notifications.show({ title: 'Login successful', message: `${role}: ${character}`, color: 'accent' })
    } else if (auth === 'error') {
      notifications.show({ title: 'Login failed', message: params.get('message') ?? 'unknown error', color: 'danger' })
    } else if (gate === 'success') {
      notifications.show({
        title: 'Access granted', message: `Logged in as ${params.get('character')}`, color: 'accent',
      })
    } else if (gate === 'denied') {
      notifications.show({
        title: 'Access denied',
        message: 'This character/corp/alliance is not on the access allowlist.',
        color: 'danger', autoClose: false,
      })
    } else if (gate === 'error') {
      notifications.show({ title: 'Login failed', message: params.get('message') ?? 'unknown error', color: 'danger' })
    } else {
      return
    }
    params.delete('auth')
    params.delete('role')
    params.delete('character')
    params.delete('message')
    params.delete('gate')
    setParams(params, { replace: true })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  void navigate
  return null
}

// Fires once per app load (staleTime: Infinity - a page reload is the
// natural "check again" trigger, not a background timer) - a cheap HEAD
// request server-side (see production/sde.py check_for_newer_sde), so safe
// to run unconditionally. Both checks are advisory only: neither ever
// auto-refreshes anything, they just tell the user a manual action (Refresh
// SDE / Load Market Groups) is worth doing. Persistent (autoClose: false)
// since a toast that vanishes in a few seconds is easy to miss and this
// isn't something to act on immediately.
function SdeFreshnessChecker() {
  const { data } = useQuery({
    queryKey: ['production', 'sde-freshness'],
    queryFn: productionApi.sdeFreshness,
    staleTime: Infinity,
    refetchOnWindowFocus: false,
    retry: false,
  })

  useEffect(() => {
    if (!data) return
    if (data.newer_sde_available) {
      notifications.show({
        id: 'sde-newer-available',
        title: 'Newer SDE available',
        message: "CCP/Fuzzwork have published a newer Static Data Export than the one currently cached. "
          + "Click Refresh SDE (Production sidebar) to pick up new/changed items.",
        color: 'warn',
        autoClose: false,
      })
    }
    if (data.trading_universe_stale) {
      notifications.show({
        id: 'trading-universe-stale',
        title: 'Trading candidate list may be outdated',
        message: "The SDE cache was refreshed after Trading's candidate universe was last built. "
          + "New items won't show up in Trading until you re-run Load Market Groups + Filter Candidates.",
        color: 'warn',
        autoClose: false,
      })
    }
  }, [data])

  return null
}

function App() {
  return (
    <>
      <AuthRedirectHandler />
      <SdeFreshnessChecker />
      <ErrorBoundary>
        <Suspense fallback={<RouteFallback />}>
          <Routes>
            <Route path="/" element={<Landing />} />
            <Route path="/portfolio" element={<Portfolio />} />
            <Route path="/admin" element={<AdminPage />} />

            <Route path="/trading" element={<TradingLayout />}>
              <Route index element={<Shortlist />} />
              <Route path="candidates" element={<Candidates />} />
              <Route path="new-candidates" element={<NewCandidates />} />
              <Route path="history" element={<PriceHistory />} />
              <Route path="trades" element={<RealizedTrades />} />
              <Route path="unlisted-stock" element={<UnlistedStock />} />
              <Route path="undercut" element={<UndercutCheck />} />
              <Route path="settings" element={<TradingSettings />} />
            </Route>

            <Route path="/production" element={<ProductionLayout />}>
              <Route index element={<StockTargets />} />
              <Route path="market" element={<MarketStatus />} />
              <Route path="jobs" element={<Jobs />} />
              <Route path="slots" element={<Slots />} />
              <Route path="buy" element={<BuyList />} />
              <Route path="build" element={<BuildList />} />
              <Route path="asset-plan" element={<AssetPlanList />} />
              <Route path="logistics" element={<Logistics />} />
              <Route path="invention" element={<Invention />} />
              <Route path="blueprints" element={<Blueprints />} />
              <Route path="unlisted-stock" element={<ProductionUnlistedStock />} />
              <Route path="build-candidates" element={<BuildCandidates />} />
              <Route path="margin" element={<Margin />} />
              <Route path="material-tree" element={<MaterialTree />} />
              <Route path="asset-search" element={<AssetSearch />} />
              <Route path="settings" element={<ProductionSettings />} />
            </Route>

            <Route path="/doctrine" element={<DoctrineLayout />}>
              <Route index element={<Doctrines />} />
              <Route path=":doctrineId" element={<DoctrineDetail />} />
              <Route path="fittings/:fittingId" element={<FittingDetail />} />
              <Route path="contracts" element={<Contracts />} />
              <Route path="contracts/history" element={<ContractHistory />} />
              <Route path="stockpile" element={<Stockpile />} />
              <Route path="shopping-list" element={<ShoppingList />} />
              <Route path="settings" element={<DoctrineSettings />} />
            </Route>
          </Routes>
        </Suspense>
      </ErrorBoundary>
    </>
  )
}

export default App
