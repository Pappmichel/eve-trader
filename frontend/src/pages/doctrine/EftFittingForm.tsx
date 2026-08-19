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
export function EftFittingForm({ initialRawEft = '', children }: {
  initialRawEft?: string
  children: (args: { preview: ParsedFittingPreview; rawEft: string }) => ReactNode
}) {
  const [rawEft, setRawEft] = useState(initialRawEft)
  const [preview, setPreview] = useState<ParsedFittingPreview | null>(null)
  const parse = useAction('Parse Fitting', () => doctrineApi.parseFitting(rawEft))

  return (
    <Stack>
      <Textarea label="EFT fitting text" placeholder="[Rifter, My Fit]&#10;..." value={rawEft}
        onChange={(e) => { setRawEft(e.currentTarget.value); setPreview(null) }} minRows={10} autosize maxRows={20}
        styles={{ input: { fontFamily: 'monospace' } }} />

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
          {children({ preview, rawEft })}
        </>
      )}
    </Stack>
  )
}
