import type { ColumnFiltersState, ColumnPinningState, SortingState, VisibilityState } from '@tanstack/react-table'

// Saved views and column order for DataTable, persisted per `tableId` in
// localStorage next to the existing column-visibility entry. Every read/write
// is wrapped: private browsing or a full quota just means "not persisted",
// never a crashed page.

export interface SavedView {
  name: string
  sorting: SortingState
  columnVisibility: VisibilityState
  columnOrder: string[]
  globalFilter: string
  columnFilters?: ColumnFiltersState
  columnSizing?: Record<string, number>
  columnPinning?: ColumnPinningState
  // Page-owned state outside the table (e.g. Shortlist's status/category
  // filters), opaque to DataTable - handed back to the page's `apply`.
  extra?: unknown
}

export const MAX_SAVED_VIEWS = 20

const viewsKey = (tableId: string) => `datatable:${tableId}:views`
const orderKey = (tableId: string) => `datatable:${tableId}:order`

function readJson<T>(key: string, fallback: T): T {
  try {
    const raw = localStorage.getItem(key)
    return raw ? (JSON.parse(raw) as T) : fallback
  } catch {
    return fallback
  }
}

function writeJson(key: string, value: unknown) {
  try {
    localStorage.setItem(key, JSON.stringify(value))
  } catch {
    // quota exceeded / unavailable - not persisted this session
  }
}

export function loadViews(tableId: string | undefined): SavedView[] {
  if (!tableId) return []
  const views = readJson<unknown>(viewsKey(tableId), [])
  return Array.isArray(views)
    ? views.filter((v): v is SavedView => !!v && typeof (v as SavedView).name === 'string')
    : []
}

export function saveViews(tableId: string | undefined, views: SavedView[]) {
  if (tableId) writeJson(viewsKey(tableId), views)
}

// Insert or replace by name (case-sensitive), newest last, capped.
export function upsertView(views: SavedView[], view: SavedView): SavedView[] {
  const without = views.filter((v) => v.name !== view.name)
  return [...without, view].slice(-MAX_SAVED_VIEWS)
}

export function loadColumnOrder(tableId: string | undefined): string[] {
  if (!tableId) return []
  const order = readJson<unknown>(orderKey(tableId), [])
  return Array.isArray(order) ? order.filter((x): x is string => typeof x === 'string') : []
}

export function saveColumnOrder(tableId: string | undefined, order: string[]) {
  if (tableId) writeJson(orderKey(tableId), order)
}

// Move `id` one step up (-1) or down (+1) within `ids`; no-op at the ends.
export function moveColumn(ids: string[], id: string, delta: -1 | 1): string[] {
  const from = ids.indexOf(id)
  const to = from + delta
  if (from < 0 || to < 0 || to >= ids.length) return ids
  const next = [...ids]
  ;[next[from], next[to]] = [next[to], next[from]]
  return next
}

// Column widths, persisted per table like visibility and order.
export type ColumnSizes = Record<string, number>

const sizesKey = (tableId: string) => `datatable:${tableId}:sizes`

export function loadColumnSizes(tableId: string | undefined): ColumnSizes {
  if (!tableId) return {}
  const raw = readJson<unknown>(sizesKey(tableId), {})
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return {}
  const out: ColumnSizes = {}
  for (const [id, v] of Object.entries(raw)) if (typeof v === 'number' && Number.isFinite(v)) out[id] = v
  return out
}

export function saveColumnSizes(tableId: string | undefined, sizes: ColumnSizes) {
  if (tableId) writeJson(sizesKey(tableId), sizes)
}

// Move `id` to the position currently held by `targetId` (the drag-and-drop
// result); no-op when either is unknown or they are the same.
export function moveColumnTo(ids: string[], id: string, targetId: string): string[] {
  const from = ids.indexOf(id)
  const to = ids.indexOf(targetId)
  if (from < 0 || to < 0 || from === to) return ids
  const next = ids.filter((x) => x !== id)
  next.splice(to, 0, id)
  return next
}

export const MIN_COLUMN_WIDTH = 60
export const MAX_COLUMN_WIDTH = 800
export const KEYBOARD_RESIZE_STEP = 10
