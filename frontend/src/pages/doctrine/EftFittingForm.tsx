import { useState, type ReactNode } from 'react'
import { Stack, Textarea, Button, Alert, Text } from '@mantine/core'

import { doctrineApi } from '../../api/client'
import type { ParsedFittingPreview } from '../../api/types'
import { useAction } from '../../hooks/useAction'

// Shared EFT-paste + parse-preview flow used by both "Add Fitting"
// (DoctrineDetail.tsx) and "Edit Fitting" (FittingDetail.tsx) - the two
// differ only in what happens after a successful preview (Add also asks
// for contract/stockpile targets; Edit just re-parses raw_eft in place),
// so those bits are left to the caller via the render-prop children.
//
// Fuel Bay / Ship Maintenance Bay (GitHub issue #18) are two more plain-text
// lists next to the main EFT paste, not part of it - standard EFT export
// text has no syntax for either (CCP's Fitting Formats spec only covers
// slots/drones/cargo), so a capital fit's bay contents are entered
// separately and parsed by doctrine/parser.py's parse_bay_items instead of
// parse_fitting. Left blank for a non-capital fit - both are optional.
export function EftFittingForm({ initialRawEft = '', initialFuelBayText = '',
  initialShipMaintenanceBayText = '', children }: {
  initialRawEft?: string
  initialFuelBayText?: string
  initialShipMaintenanceBayText?: string
  children: (args: {
    preview: ParsedFittingPreview
    rawEft: string
    fuelBayText: string
    shipMaintenanceBayText: string
  }) => ReactNode
}) {
  const [rawEft, setRawEft] = useState(initialRawEft)
  const [fuelBayText, setFuelBayText] = useState(initialFuelBayText)
  const [shipMaintenanceBayText, setShipMaintenanceBayText] = useState(initialShipMaintenanceBayText)
  const [preview, setPreview] = useState<ParsedFittingPreview | null>(null)
  const parse = useAction('Parse Fitting', () => doctrineApi.parseFitting(rawEft))

  return (
    <Stack>
      <Textarea label="EFT fitting text" placeholder="[Rifter, My Fit]&#10;..." value={rawEft}
        onChange={(e) => { setRawEft(e.currentTarget.value); setPreview(null) }} minRows={10} autosize maxRows={20}
        styles={{ input: { fontFamily: 'monospace' } }} />

      <Textarea
        label="Fuel Bay contents (capital ships only, optional)"
        description="One item per line - 'Item Name' or 'Item Name xN'. Not part of standard EFT export text, entered separately."
        placeholder="Strontium Clathrates x2500&#10;Helium Isotopes x16666"
        value={fuelBayText} onChange={(e) => setFuelBayText(e.currentTarget.value)}
        minRows={2} autosize maxRows={8} styles={{ input: { fontFamily: 'monospace' } }} />

      <Textarea
        label="Ship Maintenance Bay contents (capital ships only, optional)"
        description="One item per line - 'Item Name' or 'Item Name xN'."
        placeholder="Tayra&#10;Hoarder"
        value={shipMaintenanceBayText} onChange={(e) => setShipMaintenanceBayText(e.currentTarget.value)}
        minRows={2} autosize maxRows={8} styles={{ input: { fontFamily: 'monospace' } }} />

      {!preview && (
        <Button variant="default" disabled={!rawEft.trim()} loading={parse.isPending}
          onClick={() => parse.mutate(undefined, { onSuccess: (r) => setPreview(r as ParsedFittingPreview) })}>
          Preview
        </Button>
      )}

      {preview && (
        <>
          <Alert color="accent" variant="light">
            Hull: <b>{preview.hull_name}</b> — {preview.items.length} item(s), {preview.issues.length} warning(s)
          </Alert>
          {preview.issues.length > 0 && (
            <Stack gap={4}>
              {preview.issues.map((iss, i) => (
                <Text key={i} size="xs" c="warn">Line {iss.line_no}: {iss.message}</Text>
              ))}
            </Stack>
          )}
          {children({ preview, rawEft, fuelBayText, shipMaintenanceBayText })}
        </>
      )}
    </Stack>
  )
}
