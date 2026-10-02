// Export formats for DataTable. Pure functions over a small matrix (labels +
// raw values) so they are easy to test; DataTable only builds the matrix from
// the visible, filtered, sorted rows and triggers the download / clipboard copy.

export interface ExportColumn {
  id: string
  label: string
  // Which column holds what for the in-game list ("Item<TAB>Qty").
  role?: 'item' | 'qty'
}

export interface ExportMatrix {
  columns: ExportColumn[]
  rows: unknown[][]
}

// Anything that is not a plain scalar (React nodes, objects) exports as empty.
function scalar(value: unknown): string | number | boolean | null {
  if (typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean') return value
  return null
}

// Spreadsheet formula injection: a text cell starting with = + @ (or a minus
// that is not a number) would be evaluated by Excel. Names come from game data
// we do not control, so neutralise them with a leading apostrophe.
export function guardFormula(text: string): string {
  return /^[=+@\t\r]/.test(text) || /^-(?!\d|\.\d)/.test(text) ? `'${text}` : text
}

function textCell(value: unknown): string {
  const v = scalar(value)
  if (v === null) return ''
  return typeof v === 'string' ? guardFormula(v) : String(v)
}

export function toCsv(m: ExportMatrix): string {
  // Always quotes (never has to special-case which fields need it), doubles inner quotes.
  const field = (value: unknown) => `"${textCell(value).replace(/"/g, '""')}"`
  const header = m.columns.map((c) => field(c.label)).join(',')
  const body = m.rows.map((r) => r.map(field).join(',')).join('\r\n')
  return `${header}\r\n${body}`
}

// Tab-separated: pastes straight into Excel / Google Sheets.
export function toTsv(m: ExportMatrix): string {
  const clean = (value: unknown) => textCell(value).replace(/[\t\r\n]+/g, ' ')
  return [m.columns.map((c) => clean(c.label)).join('\t'), ...m.rows.map((r) => r.map(clean).join('\t'))].join('\n')
}

export function toMarkdown(m: ExportMatrix): string {
  const clean = (value: unknown) => textCell(value).replace(/\|/g, '\\|').replace(/[\r\n]+/g, ' ')
  const line = (cells: string[]) => `| ${cells.join(' | ')} |`
  return [
    line(m.columns.map((c) => clean(c.label))),
    line(m.columns.map(() => '---')),
    ...m.rows.map((r) => line(r.map(clean))),
  ].join('\n')
}

// Array of objects keyed by column label (raw values). Duplicate labels get
// their column id appended so no value is overwritten.
export function toJson(m: ExportMatrix): string {
  const seen = new Set<string>()
  const keys = m.columns.map((c) => {
    const key = seen.has(c.label) ? `${c.label} (${c.id})` : c.label
    seen.add(c.label)
    return key
  })
  const objects = m.rows.map((r) => Object.fromEntries(keys.map((k, i) => [k, scalar(r[i])])))
  return JSON.stringify(objects, null, 2)
}

// "Item<TAB>Qty" per line, for EVE's multibuy window and contracts. Needs one
// item column and one quantity column; rows without a positive whole quantity
// are skipped. Returns null when the table has no such pair.
export function toGameList(m: ExportMatrix): string | null {
  const item = m.columns.findIndex((c) => c.role === 'item')
  const qty = m.columns.findIndex((c) => c.role === 'qty')
  if (item < 0 || qty < 0) return null
  const lines: string[] = []
  for (const r of m.rows) {
    const name = scalar(r[item])
    const n = Number(scalar(r[qty]))
    if (typeof name !== 'string' || name.trim() === '' || !Number.isFinite(n) || n < 1) continue
    lines.push(`${name.trim()}\t${Math.round(n)}`)
  }
  return lines.length > 0 ? lines.join('\n') : null
}

export function hasGameList(m: ExportMatrix): boolean {
  return toGameList(m) !== null
}

// Header names / account keys that identify the item-name and quantity columns
// when a column does not say so itself via `meta.exportRole`.
const ITEM_HEADER = /^(item|item name|name|type|product|material|mineral|ship)$/i
const ITEM_KEY = /(^|_)(type_)?name$|^item$/
const QTY_HEADER = /^(quantity|qty|required|required quantity|needed|missing|total qty|units|amount)$/i

export function inferRole(header: unknown, accessorKey: unknown): 'item' | 'qty' | undefined {
  const label = typeof header === 'string' ? header.trim() : ''
  const key = typeof accessorKey === 'string' ? accessorKey : ''
  if (QTY_HEADER.test(label)) return 'qty'
  if (ITEM_HEADER.test(label) || ITEM_KEY.test(key)) return 'item'
  return undefined
}

// Keep only the first item and the first quantity column; later matches (a second
// "Name" column, "Amount" next to "Quantity") must not override them.
export function firstRolesOnly<T extends { role?: 'item' | 'qty' }>(columns: T[]): T[] {
  let item = false
  let qty = false
  return columns.map((c) => {
    if (c.role === 'item' && !item) { item = true; return c }
    if (c.role === 'qty' && !qty) { qty = true; return c }
    return { ...c, role: undefined }
  })
}

// Real .xlsx: numbers stay numbers, bold header, widths from the content. The
// writer is loaded on demand so it is not part of the normal bundle.
export async function toXlsxBlob(m: ExportMatrix, sheetName = 'Export'): Promise<Blob> {
  const { default: writeXlsxFile } = await import('write-excel-file/universal')
  const cell = (value: unknown) => {
    const v = scalar(value)
    if (v === null) return { value: null }
    if (typeof v === 'number') return { value: v, type: Number }
    if (typeof v === 'boolean') return { value: v, type: Boolean }
    return { value: v, type: String }
  }
  const data = [
    m.columns.map((c) => ({ value: c.label, type: String, fontWeight: 'bold' as const })),
    ...m.rows.map((r) => r.map(cell)),
  ]
  const columns = m.columns.map((c, i) => {
    const longest = m.rows.reduce((max, r) => Math.max(max, String(scalar(r[i]) ?? '').length), c.label.length)
    return { width: Math.min(50, Math.max(8, longest + 2)) }
  })
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  return (writeXlsxFile as any)(data, { columns, sheet: sheetName.slice(0, 31) }).toBlob()
}
