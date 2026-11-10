import { moduleReprocessingApi } from '../../api/client'
import { ImportPlanner } from '../refining/importPlan'

export default function ModuleImport() {
  return (
    <ImportPlanner
      storageKey="module-import"
      showCategory
      planImport={moduleReprocessingApi.planImport}
      hint={(
        <>
          Enter how much ISK you want to invest, how much volume you can haul, or both. The list picks the most
          profitable active shortlist modules and compressed ore/ice, buying real sell orders cheapest first, only
          while an order still meets this tool's minimum margin and profit. Modules are priced like the Shortlist;
          ore like Ore &amp; Minerals (input hub, Trading haul cost, its refining yield).
        </>
      )}
    />
  )
}
