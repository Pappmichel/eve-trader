import { ToolOverview } from '../../components/ToolOverview'

export default function Overview() {
  return (
    <ToolOverview
      description="Buys and sells on Jita's own order book, profiting from the bid-ask spread - a separate
        model from Trading's Jita-import/C-J-sell business, and from Production's buy-vs-build planning."
      noEsiFeatures={[
        { label: 'Settings', note: 'broker fee/sales tax/spread thresholds are manually configured' },
      ]}
      noEsiFootnote="Shortlist discovery uses Goonmetrics' public Jita price dump to find candidates, then
        confirms live via Jita's own public order book - neither step needs a login."
      esiFeatureGroups={[
        {
          role: 'Trader login',
          features: [
            { label: 'Undercut Check', note: 'compares your own open Jita orders against the live order book, both buy and sell' },
            { label: 'Skills panel (Settings)', note: 'live trade-skill levels and derived order-slot count' },
          ],
        },
      ]}
    />
  )
}
