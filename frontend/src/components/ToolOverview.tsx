import { Alert, Badge, Card, List, Stack, Text, Title } from '@mantine/core'
import { IconWifi, IconWifiOff } from '@tabler/icons-react'

export interface FeatureEntry {
  label: string
  note?: string
}

export interface EsiFeatureGroup {
  /** e.g. "Seller login", "Producer login" */
  role: string
  features: FeatureEntry[]
}

interface ToolOverviewProps {
  description: string
  noEsiFeatures: FeatureEntry[]
  esiFeatureGroups: EsiFeatureGroup[]
  /** Extra note shown under the "works without ESI" list - e.g. explaining
   * a Goonmetrics failsafe, or that a page needs a prior sync to show data. */
  noEsiFootnote?: string
}

// Shared building block for each tool's landing page - same content shape
// for Trading/Production/Doctrine/Ore & Minerals, so "what works without an
// EVE login, and what needs one" is answered the same way everywhere rather
// than each tool inventing its own layout.
export function ToolOverview({ description, noEsiFeatures, esiFeatureGroups, noEsiFootnote }: ToolOverviewProps) {
  return (
    <Stack>
      <Text c="dimmed">{description}</Text>

      <Card withBorder>
        <Title order={4} mb="xs">
          <IconWifiOff size={18} style={{ verticalAlign: 'text-bottom', marginRight: 6 }} />
          Works without any EVE character logged in
        </Title>
        <List spacing="xs" size="sm">
          {noEsiFeatures.map((f) => (
            <List.Item key={f.label}>
              <Text span fw={600}>{f.label}</Text>
              {f.note && <Text span c="dimmed"> — {f.note}</Text>}
            </List.Item>
          ))}
        </List>
        {noEsiFootnote && (
          <Alert color="accent" variant="light" mt="md">{noEsiFootnote}</Alert>
        )}
      </Card>

      <Card withBorder>
        <Title order={4} mb="xs">
          <IconWifi size={18} style={{ verticalAlign: 'text-bottom', marginRight: 6 }} />
          Needs an EVE character logged in
        </Title>
        <Stack gap="md">
          {esiFeatureGroups.map((group) => (
            <div key={group.role}>
              <Badge color="warn" variant="light" mb={6}>{group.role}</Badge>
              <List spacing="xs" size="sm">
                {group.features.map((f) => (
                  <List.Item key={f.label}>
                    <Text span fw={600}>{f.label}</Text>
                    {f.note && <Text span c="dimmed"> — {f.note}</Text>}
                  </List.Item>
                ))}
              </List>
            </div>
          ))}
        </Stack>
      </Card>
    </Stack>
  )
}
