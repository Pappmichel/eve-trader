import { describe, expect, it } from 'vitest'

import {
  firstRolesOnly, guardFormula, inferRole, toCsv, toGameList, toJson, toMarkdown, toTsv, toXlsxBlob,
  type ExportMatrix,
} from './dataTableExport'

const m: ExportMatrix = {
  columns: [{ id: 'item', label: 'Item', role: 'item' }, { id: 'qty', label: 'Quantity', role: 'qty' }, { id: 'p', label: 'Price' }],
  rows: [['Tritanium', 1500, 4.5], ['Mexallon "Pro"', 20, null], ['Pyerite | X', 0, 1]],
}

describe('toCsv', () => {
  it('quotes every field, doubles inner quotes, uses CRLF', () => {
    const csv = toCsv(m)
    expect(csv.split('\r\n')[0]).toBe('"Item","Quantity","Price"')
    expect(csv).toContain('"Mexallon ""Pro""","20",""')
  })
  it('German Excel variant: semicolon, BOM and decimal comma', () => {
    const csv = toCsv(m, { delimiter: ';', bom: true, decimalComma: true })
    expect(csv.startsWith('﻿"Item";"Quantity";"Price"')).toBe(true)
    expect(csv).toContain('"Tritanium";"1500";"4,5"')
  })
  it('neutralises spreadsheet formulas in text but not negative numbers', () => {
    const csv = toCsv({ columns: [{ id: 'a', label: 'A' }], rows: [['=1+1'], ['+x'], ['@SUM'], ['-abc'], [-5], ['-5']] })
    expect(csv).toContain(`"'=1+1"`)
    expect(csv).toContain(`"'+x"`)
    expect(csv).toContain(`"'@SUM"`)
    expect(csv).toContain(`"'-abc"`)
    expect(csv).toContain('"-5"')
    expect(guardFormula('Plain')).toBe('Plain')
  })
})

describe('toTsv / toMarkdown / toJson', () => {
  it('tsv has one line per row and strips tabs/newlines inside cells', () => {
    expect(toTsv({ columns: [{ id: 'a', label: 'A' }, { id: 'b', label: 'B' }], rows: [['x\ty', 'l1\nl2']] }))
      .toBe('A\tB\nx y\tl1 l2')
  })
  it('markdown escapes pipes and has a separator row', () => {
    const md = toMarkdown(m).split('\n')
    expect(md[0]).toBe('| Item | Quantity | Price |')
    expect(md[1]).toBe('| --- | --- | --- |')
    expect(md[4]).toBe('| Pyerite \\| X | 0 | 1 |')
  })
  it('json keeps raw values and disambiguates duplicate labels', () => {
    const json = JSON.parse(toJson({ columns: [{ id: 'a', label: 'N' }, { id: 'b', label: 'N' }], rows: [[1, 'x']] }))
    expect(json).toEqual([{ N: 1, 'N (b)': 'x' }])
    expect(JSON.parse(toJson(m))[1]).toEqual({ Item: 'Mexallon "Pro"', Quantity: 20, Price: null })
  })
})

describe('toGameList', () => {
  it('lists item and whole quantity, skipping empty or non-positive rows', () => {
    expect(toGameList(m)).toBe('Tritanium\t1500\nMexallon "Pro"\t20')
  })
  it('is null without an item+quantity pair or without usable rows', () => {
    expect(toGameList({ columns: [{ id: 'a', label: 'A' }], rows: [['x']] })).toBeNull()
    expect(toGameList({ columns: m.columns, rows: [['Tritanium', 0, 1]] })).toBeNull()
  })
  it('rounds fractional quantities', () => {
    expect(toGameList({ columns: m.columns, rows: [['Tritanium', 2.6, 1]] })).toBe('Tritanium\t3')
  })
})

describe('column role detection', () => {
  it('infers item and quantity columns from header or accessor key', () => {
    expect(inferRole('Item', 'item')).toBe('item')
    expect(inferRole('Type', 'type_name')).toBe('item')
    expect(inferRole('Required Quantity', 'required_qty')).toBe('qty')
    expect(inferRole('Missing', 'missing')).toBe('qty')
    expect(inferRole('Margin', 'margin')).toBeUndefined()
  })
  it('keeps only the first item and first quantity column', () => {
    const roles = firstRolesOnly([
      { role: 'item' as const }, { role: 'item' as const }, { role: 'qty' as const }, { role: 'qty' as const },
    ])
    expect(roles.map((r) => r.role)).toEqual(['item', undefined, 'qty', undefined])
  })
})

describe('toXlsxBlob', () => {
  it('produces a real xlsx (zip) with the data', async () => {
    const blob = await toXlsxBlob(m, 'Test')
    expect(blob.size).toBeGreaterThan(500)
    const buffer = await new Promise<ArrayBuffer>((resolve) => {
      const reader = new FileReader()
      reader.onload = () => resolve(reader.result as ArrayBuffer)
      reader.readAsArrayBuffer(blob)
    })
    const bytes = new Uint8Array(buffer)
    expect(String.fromCharCode(bytes[0], bytes[1])).toBe('PK') // zip magic
  })
})
