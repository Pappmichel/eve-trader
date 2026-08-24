import { ToolOverview } from '../../components/ToolOverview'

export default function Overview() {
  return (
    <ToolOverview
      description="Fleet doctrine fitting management, contract validation against C-J's stock contracts,
        and stockpile tracking."
      noEsiFeatures={[
        { label: 'Fittings', note: 'parse, add, and edit EFT fittings' },
        { label: 'Doctrines', note: 'create and manage doctrine groupings' },
      ]}
      noEsiFootnote={
        'Status, Stockpile, Shopping List, Contract History, and List Contracts all read from your last ' +
        'Sync rather than calling ESI live - they stay empty (or stale) until you sync at least once.'
      }
      esiFeatureGroups={[
        {
          role: 'Doctrine login',
          features: [
            { label: 'Contract Sync', note: 'pulls corp contracts to validate against expected doctrine fits' },
          ],
        },
        {
          role: 'Doctrine-Assets login',
          features: [
            { label: 'Asset Sync', note: 'pulls character/corp assets for stockpile tracking - a separate character/scope from Contract Sync' },
          ],
        },
      ]}
    />
  )
}
