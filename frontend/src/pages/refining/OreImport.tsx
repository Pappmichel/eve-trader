import { refiningApi } from '../../api/client'
import { ImportPlanner } from './importPlan'

export default function OreImport() {
  return (
    <ImportPlanner
      storageKey="ore-import"
      showCategory={false}
      planImport={refiningApi.planImport}
      hint={(
        <>
          Enter how much ISK you want to invest, how much volume you can haul, or both. The list picks the most
          profitable active shortlist ores and buys real hub sell orders cheapest first, only while an order still
          meets your minimum margin and profit (Trading Settings). Prices, freight, yield and refining tax are the
          same as on the Ore Shortlist.
        </>
      )}
    />
  )
}
