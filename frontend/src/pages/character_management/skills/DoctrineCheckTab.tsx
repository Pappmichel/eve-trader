import { Fragment, useState } from 'react'
import { Alert, Badge, Loader, Stack, Table, Text, UnstyledButton } from '@mantine/core'
import { useQuery } from '@tanstack/react-query'

import { ApiError, charSkillsApi } from '../../../api/client'
import type { DoctrineCheckFitting } from '../../../api/types'
import { duration, qty } from '../../../format'

const ROMAN = ['0', 'I', 'II', 'III', 'IV', 'V']

function fittingLabel(f: DoctrineCheckFitting): string {
  return f.variant_label ? `${f.name} (${f.variant_label})` : f.name
}

// Which characters can fly which doctrine fitting. Needs the Skills grant (this
// page) and the Doctrine grant (the fittings) - the Skills page only offers the
// tab when both are held.
export function DoctrineCheckTab() {
  const { data, isLoading, error } = useQuery({
    queryKey: ['char-skills', 'doctrine-check'], queryFn: charSkillsApi.doctrineCheck, retry: false,
  })
  const [open, setOpen] = useState<string | null>(null)

  if (isLoading) return <Loader color="accent" />
  if (error || !data) {
    return <Alert color="red" title="Could not run the check">{error instanceof ApiError ? error.message : 'Request failed.'}</Alert>
  }
  if (!data.sde_ready) {
    return (
      <Alert color="yellow" title="Skill requirements are not loaded yet">
        The SDE cache has no skill requirements. Ask an admin to run an SDE preview and apply it once.
      </Alert>
    )
  }
  if (data.fittings.length === 0) return <Text c="dimmed">No active doctrine fittings to check.</Text>
  if (data.characters.length === 0) {
    return <Text c="dimmed">No character is shared with Skills. Tick Skills for a character on the Characters page.</Text>
  }

  const cols = data.characters
  return (
    <Stack gap="sm">
      <Text size="xs" c="dimmed">
        Click a row to see the missing skills. Training times are estimates from each character&apos;s current
        attributes, without implants or boosters.
      </Text>
      <Table.ScrollContainer minWidth={400}>
        <Table withTableBorder striped>
          <Table.Thead>
            <Table.Tr>
              <Table.Th>Fitting</Table.Th>
              <Table.Th>Skills</Table.Th>
              {cols.map((c) => <Table.Th key={c.character_id}>{c.character_name}</Table.Th>)}
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {data.fittings.map((f) => {
              const expanded = open === f.fitting_id
              return (
                <Fragment key={f.fitting_id}>
                  <Table.Tr>
                    <Table.Td>
                      <UnstyledButton onClick={() => setOpen(expanded ? null : f.fitting_id)} aria-expanded={expanded}>
                        <Text size="sm" fw={600}>{fittingLabel(f)}</Text>
                        <Text size="xs" c="dimmed">{[f.hull_name, f.doctrine_name].filter(Boolean).join(' · ')}</Text>
                      </UnstyledButton>
                    </Table.Td>
                    <Table.Td>{f.required_skills}</Table.Td>
                    {cols.map((c) => {
                      const r = f.characters.find((x) => x.character_id === c.character_id)
                      if (!r) return <Table.Td key={c.character_id}>–</Table.Td>
                      return (
                        <Table.Td key={c.character_id}>
                          {r.can_fly
                            ? <Badge color="green">Can fly</Badge>
                            : <Badge color="orange">{r.missing.length} missing · {r.train_seconds === null ? 'time unknown' : `~${duration(r.train_seconds)}`}</Badge>}
                        </Table.Td>
                      )
                    })}
                  </Table.Tr>
                  {expanded && (
                    <Table.Tr>
                      <Table.Td colSpan={2 + cols.length}>
                        <Stack gap="xs">
                          {cols.map((c) => {
                            const r = f.characters.find((x) => x.character_id === c.character_id)
                            if (!r || r.can_fly) return null
                            return (
                              <div key={c.character_id}>
                                <Text size="sm" fw={600}>{c.character_name}</Text>
                                {r.missing.map((m) => (
                                  <Text size="xs" key={m.skill_id}>
                                    {m.name} {ROMAN[m.needed] ?? m.needed} (has {ROMAN[m.have] ?? m.have})
                                    {m.sp_remaining !== null && ` – ${qty(m.sp_remaining)} SP`}
                                  </Text>
                                ))}
                              </div>
                            )
                          })}
                          {f.characters.every((r) => r.can_fly) && <Text size="sm">Everyone shared can fly this fitting.</Text>}
                        </Stack>
                      </Table.Td>
                    </Table.Tr>
                  )}
                </Fragment>
              )
            })}
          </Table.Tbody>
        </Table>
      </Table.ScrollContainer>
      {data.hidden_characters.length > 0 && (
        <Text size="xs" c="dimmed">
          Not checked (Skills not shared): {data.hidden_characters.map((c) => c.character_name).join(', ')}
        </Text>
      )}
    </Stack>
  )
}
