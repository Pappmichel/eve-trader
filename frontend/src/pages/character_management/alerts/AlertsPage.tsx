import { useEffect } from 'react'
import {
  Alert, Anchor, Badge, Button, Checkbox, Container, Group, Loader, NumberInput, Paper, Stack, Switch, Table, Text, Title,
} from '@mantine/core'
import { notify } from '../../../notify'
import { IconArrowLeft } from '@tabler/icons-react'
import { Link, useSearchParams } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { ApiError, charAlertsApi } from '../../../api/client'
import type { AlertSubscription, AlertType } from '../../../api/types'

const KEY = ['char-alerts', 'settings']
const LINK_RESULT: Record<string, { color: string; title: string; message: string }> = {
  linked: { color: 'accent', title: 'Discord linked', message: 'You can now switch alerts on.' },
  cancelled: { color: 'yellow', title: 'Discord link cancelled', message: 'Nothing was changed.' },
  error: { color: 'danger', title: 'Discord link failed', message: 'The link request expired or was rejected. Try again.' },
}

// Labels per alert type. The PI ones read a colony snapshot (Planetary Industry
// shared with Discord Alerts); pad full / inputs empty are estimates from it.
const ALERT_LABELS: Record<AlertType, { label: string; aria: string }> = {
  skillqueue_empty: { label: 'Skill queue ends', aria: 'skill queue' },
  mail_new: { label: 'New mail', aria: 'new mail' },
  pi_extractor_expiry: { label: 'PI: extractor program ends', aria: 'PI extractor' },
  pi_pad_full: { label: 'PI: launchpads nearly full (estimate)', aria: 'PI pad full' },
  pi_inputs_empty: { label: 'PI: factory inputs nearly used up (estimate)', aria: 'PI inputs empty' },
}
const ALERT_TYPES: AlertType[] = ['skillqueue_empty', 'mail_new', 'pi_extractor_expiry', 'pi_pad_full', 'pi_inputs_empty']
const LEAD_TIME_TYPES = new Set<AlertType>(['skillqueue_empty', 'pi_extractor_expiry', 'pi_pad_full', 'pi_inputs_empty'])

function errorMessage(e: unknown): string {
  return e instanceof ApiError ? e.message : 'Something went wrong.'
}

function AlertRow(props: {
  characterId: number; label: string; type: AlertType; sub: AlertSubscription; linked: boolean; pending: boolean
  onChange: (change: { enabled: boolean; include_content?: boolean; lead_hours?: number }) => void
}) {
  const { sub, linked, pending, type } = props
  const blocked = !sub.enabled && (!linked || !sub.shared)
  const hint = !linked ? 'Link Discord first' : !sub.shared ? 'Share this data with Discord Alerts first' : undefined
  return (
    <Stack gap={4}>
      <Group gap="sm" wrap="nowrap">
        <Switch
          aria-label={`${props.label} ${ALERT_LABELS[type].aria} alert`}
          checked={sub.enabled} disabled={blocked || pending} label={ALERT_LABELS[type].label}
          onChange={(e) => props.onChange({
            enabled: e.currentTarget.checked, include_content: sub.include_content, lead_hours: sub.lead_hours,
          })}
        />
        {hint && <Text size="xs" c="dimmed">{hint}</Text>}
      </Group>
      {LEAD_TIME_TYPES.has(type) && sub.enabled && (
        <NumberInput
          size="xs" w={200} min={1} max={168} value={sub.lead_hours} suffix=" h before" disabled={pending}
          aria-label={type === 'skillqueue_empty' ? `${props.label} lead time in hours` : `${props.label} ${ALERT_LABELS[type].aria} lead time in hours`}
          onChange={(v) => typeof v === 'number' && v >= 1 && v <= 168 && props.onChange({ enabled: true, lead_hours: v })}
        />
      )}
      {type === 'mail_new' && sub.enabled && (
        <Stack gap={2}>
          <Checkbox
            size="xs" checked={sub.include_content} disabled={pending}
            label="Include the mail text in the message"
            aria-label={`${props.label} include mail content`}
            onChange={(e) => props.onChange({ enabled: true, include_content: e.currentTarget.checked })}
          />
          <Text size="xs" c={sub.include_content ? 'orange' : 'dimmed'}>
            {sub.include_content
              ? 'The mail text will be sent to Discord and can not be recalled from there.'
              : 'Without this only the number of new mails, the sender and the subject are sent.'}
          </Text>
        </Stack>
      )}
    </Stack>
  )
}

export default function AlertsPage() {
  const qc = useQueryClient()
  const [params, setParams] = useSearchParams()
  const { data, isLoading, error } = useQuery({ queryKey: KEY, queryFn: charAlertsApi.settings, retry: false })

  const result = params.get('discord')
  useEffect(() => {
    if (!result) return
    const info = LINK_RESULT[result]
    if (info) notify(info)
    qc.invalidateQueries({ queryKey: KEY })
    setParams({}, { replace: true })
  }, [result, qc, setParams])

  const notifyError = (e: unknown) => notify({ title: 'Discord Alerts', message: errorMessage(e), color: 'danger' })
  const refresh = () => qc.invalidateQueries({ queryKey: KEY })

  const link = useMutation({
    mutationFn: charAlertsApi.linkStart,
    onSuccess: ({ url }) => { window.location.assign(url) },
    onError: notifyError,
  })
  const unlink = useMutation({ mutationFn: charAlertsApi.unlink, onSuccess: refresh, onError: notifyError })
  const test = useMutation({
    mutationFn: charAlertsApi.test,
    onSuccess: () => notify({ title: 'Test message sent', message: 'Check your Discord DMs.', color: 'accent' }),
    onError: notifyError,
  })
  const subscribe = useMutation({ mutationFn: charAlertsApi.setSubscription, onSuccess: refresh, onError: notifyError })

  return (
    <Container size="md" py="xl">
      <Group justify="space-between" align="flex-start" mb="lg">
        <div>
          <Text tt="uppercase" size="xs" c="dimmed" fw={600} lts={2}>Character Management</Text>
          <Title order={1}>Discord Alerts</Title>
        </div>
        <Button component={Link} to="/character-management" variant="subtle" leftSection={<IconArrowLeft size={14} />}>Back</Button>
      </Group>

      {isLoading && <Loader color="accent" />}
      {error && <Text c="red">{errorMessage(error)}</Text>}
      {data && (
        <Stack gap="lg">
          <Paper withBorder p="md">
            <Group justify="space-between">
              <div>
                <Text fw={600}>Discord account</Text>
                <Text size="sm" c="dimmed">
                  Alerts arrive as a direct message from the bot. Every alert below is off until you switch it on.
                </Text>
              </div>
              {data.linked ? <Badge color="green">Linked</Badge> : <Badge color="gray">Not linked</Badge>}
            </Group>
            {!data.bot_configured || !data.link_configured ? (
              <Alert mt="md" color="yellow" title="Not available on this server">
                The operator has not set up the Discord bot yet (DISCORD_BOT_TOKEN, DISCORD_CLIENT_ID, DISCORD_CLIENT_SECRET).
              </Alert>
            ) : (
              <Group mt="md">
                {data.linked ? (
                  <>
                    <Button variant="light" loading={test.isPending} onClick={() => test.mutate()}>Send test message</Button>
                    <Button variant="subtle" color="red" loading={unlink.isPending} onClick={() => unlink.mutate()}>
                      Unlink (switches all alerts off)
                    </Button>
                  </>
                ) : (
                  <Button loading={link.isPending} onClick={() => link.mutate()}>Link Discord account</Button>
                )}
              </Group>
            )}
          </Paper>

          <Paper withBorder p="md">
            <Text fw={600} mb={4}>Characters</Text>
            <Text size="sm" c="dimmed" mb="md">
              An alert also needs the matching data shared with Discord Alerts on the{' '}
              <Anchor component={Link} to="/character-management/characters">Characters page</Anchor>.
              Revoking the sharing stops the alert immediately.
            </Text>
            {data.characters.length === 0 ? (
              <Text size="sm" c="dimmed">No characters with ESI access yet.</Text>
            ) : (
              <Table.ScrollContainer minWidth={0}>
                <Table>
                  <Table.Tbody>
                    {data.characters.map((c) => (
                      <Table.Tr key={c.character_id}>
                        <Table.Td fw={600}>{c.character_name}</Table.Td>
                        <Table.Td>
                          <Stack gap="sm">
                            {ALERT_TYPES.filter((type) => c.alerts[type]).map((type) => (
                              <AlertRow
                                key={type} characterId={c.character_id} label={c.character_name} type={type}
                                sub={c.alerts[type]!} linked={data.linked} pending={subscribe.isPending}
                                onChange={(change) => subscribe.mutate({ character_id: c.character_id, alert_type: type, ...change })}
                              />
                            ))}
                          </Stack>
                        </Table.Td>
                      </Table.Tr>
                    ))}
                  </Table.Tbody>
                </Table>
              </Table.ScrollContainer>
            )}
          </Paper>
        </Stack>
      )}
    </Container>
  )
}
