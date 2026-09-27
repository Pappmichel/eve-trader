import { ToolOverview } from '../../components/ToolOverview'

export default function Overview() {
  return (
    <ToolOverview
      description="Imports T1/Meta modules and drones, reprocesses at C-J, and sells the resulting minerals for
        profit - Tech II, Faction, Officer, Storyline and Deadspace items are excluded, since those are almost
        always worth more sold intact than scrapped."
      noEsiFeatures={[
        { label: 'Discover', note: "scans the full T1/Meta module+drone SDE universe against a Goonmetrics current-price snapshot - never needs a login" },
        { label: 'Add to Shortlist', note: 'moves picked Discover results onto the live-tracked shortlist locally' },
      ]}
      noEsiFootnote="Refresh Shortlist reuses Trading's own Seller login for C-J's live mineral prices; a configured Goonmetrics fallback market keeps it working without one, at reduced precision."
      esiFeatureGroups={[
        {
          role: 'Seller login (shared with Trading)',
          features: [
            { label: 'Refresh Shortlist', note: 'precise, real-time purchase and C-J mineral prices (falls back to Goonmetrics if configured, see above)' },
          ],
        },
      ]}
    />
  )
}
