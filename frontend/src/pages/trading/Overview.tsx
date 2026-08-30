import { ToolOverview } from '../../components/ToolOverview'

export default function Overview() {
  return (
    <ToolOverview
      description="Buys compressed goods at Jita, imports them, and sells at C-J. Tracks a shortlist of
        candidate items, computes margins, and finds new profitable candidates via historical backtesting."
      noEsiFeatures={[
        { label: 'Load Market Groups / Filter Candidates', note: "builds the tradeable item universe from local SDE data plus public (unauthenticated) ESI market-group data" },
        { label: 'Find New Candidates (History Backtest)', note: 'Goonmetrics region-average history only' },
        { label: 'Price History', note: 'reads history already cached locally by past backtests' },
        { label: 'Shortlist Trends', note: 'pure local data - no network calls at all' },
      ]}
      noEsiFootnote="Refresh Shortlist normally needs a Seller login for C-J's live prices, but a configured Goonmetrics fallback market (Settings) keeps it working without one, at reduced precision."
      esiFeatureGroups={[
        {
          role: 'Seller login',
          features: [
            { label: 'Refresh Shortlist', note: "precise, real-time C-J structure prices (falls back to Goonmetrics if configured, see above)" },
            { label: 'Unlisted Stock Check', note: "reads your own inventory/open sell orders" },
            { label: 'Undercut Check', note: "compares against real competing orders" },
          ],
        },
        {
          role: 'Buyer + Seller login',
          features: [
            { label: 'Trade Reconciliation', note: 'matches realized wallet transactions against known trades' },
          ],
        },
      ]}
    />
  )
}
