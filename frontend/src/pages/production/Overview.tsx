import { ToolOverview } from '../../components/ToolOverview'

export default function Overview() {
  return (
    <ToolOverview
      description="Tech I/II/Reaction manufacturing planning for the same C-J structure - buy-vs-build
        decisions, stock targets, and invention planning."
      noEsiFeatures={[
        { label: 'Stock Targets', note: 'manage what you want to keep in stock' },
        { label: 'Buy List / Build List / Build List (Asset-Optimized)', note: 'buy-vs-build computation from Goonmetrics prices and local SDE data' },
        { label: 'Invention', note: 'decryptor/BPC economics estimate and the material tree' },
        { label: 'Build Candidates / Ship Margins / Margin', note: 'Goonmetrics-based profitability screening' },
      ]}
      noEsiFootnote={
        'Market Status, Industry Jobs, Character Slots, Logistics, and Blueprints all read from your last ' +
        'ESI Sync rather than calling ESI live - they stay empty until you sync at least once.'
      }
      esiFeatureGroups={[
        {
          role: 'Producer login',
          features: [
            { label: 'ESI Sync', note: 'pulls your character/corp assets, industry jobs, and blueprints - what every page above then reads' },
            { label: 'Resolve Structure Name', note: "looks up a structure's name - not public data" },
            { label: 'Unlisted Stock', note: 'checks your own inventory against open listings' },
          ],
        },
      ]}
    />
  )
}
