import { ToolOverview } from '../../components/ToolOverview'

export default function Overview() {
  return (
    <ToolOverview
      description="Imports compressed ore/ice, refines at C-J, and sells minerals for profit - and
        separately plans mineral needs for Production's build list."
      noEsiFeatures={[
        { label: 'Add Candidates (Ore Shortlist)', note: 'pulls the compressed ore/ice universe from local SDE data' },
        { label: 'Mineral Requirements / Refinable Minerals', note: 'manage what Production needs' },
        { label: 'Mineral Shopping List', note: "solves the cheapest buy-vs-refine mix using Jita's public order book and an unauthenticated Goonmetrics quote for C-J - never needs a login" },
      ]}
      noEsiFootnote={
        "Refresh Ore Shortlist and Reprocessing Quote reuse Trading's own Seller login for C-J's live prices; " +
        'if you configure an optional Goonmetrics fallback market (in Trading Settings), they keep working ' +
        'without one too, at reduced precision.'
      }
      esiFeatureGroups={[
        {
          role: 'Seller login (shared with Trading)',
          features: [
            { label: 'Refresh Ore Shortlist', note: 'precise, real-time C-J mineral prices (falls back to Goonmetrics if configured, see above)' },
            { label: 'Reprocessing Quote', note: 'same' },
          ],
        },
      ]}
    />
  )
}
