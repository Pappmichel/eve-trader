// Fixture for tests that mock `hubsApi.list` (what GET /api/hubs returns).
import type { TradeHub } from './tradingHubs'

export const TEST_HUBS: TradeHub[] = [
  { region_id: 10000002, name: 'Jita', label: 'Jita (The Forge)', solar_system_id: 30000142, station_id: 60003760 },
  { region_id: 10000043, name: 'Amarr', label: 'Amarr (Domain)', solar_system_id: 30002187, station_id: 60008494 },
  { region_id: 10000032, name: 'Dodixie', label: 'Dodixie (Sinq Laison)', solar_system_id: 30002659, station_id: 60011866 },
  { region_id: 10000030, name: 'Rens', label: 'Rens (Heimatar)', solar_system_id: 30002510, station_id: 60004588 },
]
