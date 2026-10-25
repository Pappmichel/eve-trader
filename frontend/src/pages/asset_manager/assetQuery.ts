// Labels and URL-state helpers of the Asset Manager pages (docs/ASSET_MANAGER_PLAN.md).
import type { AssetQuery } from '../../api/types'

export const SOURCE_LABELS: Record<string, string> = {
  esi: 'Assets',
  manual: 'Manual stock',
  listed: 'Listed for sale',
  contract: 'In contracts',
  in_production: 'In production',
}

export const BADGE_LABELS: Record<string, { label: string; color: string; hint: string }> = {
  asset_safety: { label: 'Asset safety', color: 'red', hint: 'In an Asset Safety wrap - not usable until delivered.' },
  delivery: { label: 'Delivery', color: 'orange', hint: 'In a delivery hangar - not counted as stock by the planners.' },
  corp_market: { label: 'Corp market', color: 'orange', hint: 'In the corp market hangar.' },
  assembled: { label: 'Assembled', color: 'grape', hint: 'An assembled ship - valued, but never counted as stock.' },
  fitted: { label: 'Fitted', color: 'grape', hint: 'Fitted to a ship, in a drone or fighter bay - not stock.' },
  bpo: { label: 'BPO', color: 'blue', hint: 'Blueprint original.' },
  bpc: { label: 'BPC', color: 'cyan', hint: 'Blueprint copy - valued by manual price or invention cost per run.' },
  manual: { label: 'Manual', color: 'gray', hint: 'Manual stock entered in Production.' },
  listed: { label: 'Listed', color: 'teal', hint: 'In an open sell order - not in the ESI asset list.' },
  contract: { label: 'Contract', color: 'teal', hint: 'In an outstanding item-exchange contract you issued.' },
  in_production: { label: 'In production', color: 'indigo', hint: 'Output of a running job - does not exist yet.' },
}

export function securityColor(sec: number | null | undefined): string {
  if (sec == null) return 'dimmed'
  if (sec >= 0.5) return 'teal'
  if (sec > 0) return 'orange'
  return 'red'
}

// ------------------------------------------------------------ URL state
// Filters live in the query string, so the back button and bookmarks work.
// A link only opens for the same tenant (plan review I-3).
const LIST_KEYS = ['owners', 'category_ids', 'group_ids', 'type_ids', 'sources', 'hide_badges'] as const
const NUMBER_KEYS = ['region_id', 'system_id', 'location_id', 'container_item_id'] as const
const NUMBER_LISTS = new Set(['category_ids', 'group_ids', 'type_ids'])

export const DEFAULT_QUERY: AssetQuery = { group_by: 'type_location', sort: 'value', desc: true, page: 1, page_size: 50 }

export function queryFromParams(params: URLSearchParams): AssetQuery {
  const q: AssetQuery = { ...DEFAULT_QUERY }
  const text = params.get('q')
  if (text) q.q = text
  for (const key of LIST_KEYS) {
    const values = params.getAll(key)
    if (values.length) {
      (q as Record<string, unknown>)[key] = NUMBER_LISTS.has(key) ? values.map(Number) : values
    }
  }
  for (const key of NUMBER_KEYS) {
    const v = params.get(key)
    if (v !== null && v !== '') q[key] = Number(v)
  }
  const hangar = params.get('hangar')
  if (hangar) q.hangar = hangar
  if (params.get('stock_only') === 'true') q.stock_only = true
  const groupBy = params.get('group_by')
  if (groupBy === 'none' || groupBy === 'type' || groupBy === 'type_location') q.group_by = groupBy
  const sort = params.get('sort')
  if (sort) q.sort = sort
  if (params.get('desc') === 'false') q.desc = false
  const page = Number(params.get('page'))
  if (page > 1) q.page = page
  const size = Number(params.get('page_size'))
  if (size > 0) q.page_size = size
  return q
}

export function paramsFromQuery(q: AssetQuery): URLSearchParams {
  const out = new URLSearchParams()
  for (const [key, value] of Object.entries(q)) {
    if (value === undefined || value === null || value === '' || value === false) continue
    if ((DEFAULT_QUERY as Record<string, unknown>)[key] === value) continue
    if (Array.isArray(value)) value.forEach((v) => out.append(key, String(v)))
    else out.set(key, String(value))
  }
  return out
}

/** The filter part of a query (what a saved view stores and exports use). */
export function filterPart(q: AssetQuery): AssetQuery {
  const { page: _page, ...rest } = q
  return rest
}
