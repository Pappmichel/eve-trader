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
      noEsiFootnote={
        'Refresh Shortlist normally needs a Seller login for C-J’s live prices, but if you configure an ' +
        'optional Goonmetrics fallback market (Settings → Structure market slug), it keeps working without ' +
        'one too - just with less precise best-bid/best-ask prices instead of the real order book.'
      }
      esiFeatureGroups={[
        {
          role: 'Seller login',
          features: [
            { label: 'Refresh Shortlist', note: "precise, real-time C-J structure prices (falls back to Goonmetrics if configured, see above)" },
            { label: 'Unlisted Stock Check', note: "reads your own inventory/open sell orders - no substitute exists for that" },
            { label: 'Undercut Check', note: "compares against real competing orders - deliberately has no fallback, since a wrong “not undercut” would be worse than an error" },
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
