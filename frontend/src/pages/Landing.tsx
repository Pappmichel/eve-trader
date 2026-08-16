import { Container, Title, Text, SimpleGrid, Card, Button, Stack } from '@mantine/core'
import { IconArrowRight } from '@tabler/icons-react'
import { Link } from 'react-router-dom'

export default function Landing() {
  return (
    <Container size="md" py="xl">
      <Text tt="uppercase" size="xs" c="dimmed" fw={600} lts={2}>
        C-J Import & Manufacturing
      </Text>
      <Title order={1} mb="lg">EVE Trader</Title>
      <Text c="dimmed" mb="xl">Margins, buy/build decisions and live market data in one place.</Text>

      <SimpleGrid cols={2} spacing="md">
        <Card withBorder padding="lg" radius="md">
          <Stack gap="xs">
            <Title order={3}>Trading</Title>
            <Text c="dimmed" size="sm">
              C-J import trading: candidate search, shortlist, margins, trade reconciliation.
            </Text>
            <Button component={Link} to="/trading" mt="sm" rightSection={<IconArrowRight size={14} />}>Open</Button>
          </Stack>
        </Card>

        <Card withBorder padding="lg" radius="md">
          <Stack gap="xs">
            <Title order={3}>Production</Title>
            <Text c="dimmed" size="sm">
              Stock targets, buy-vs-build decisions, buy/build lists for T2 manufacturing.
            </Text>
            <Button component={Link} to="/production" mt="sm" rightSection={<IconArrowRight size={14} />}>Open</Button>
          </Stack>
        </Card>
      </SimpleGrid>

      <Card withBorder padding="lg" radius="md" mt="md">
        <Stack gap="xs">
          <Title order={3}>Portfolio Overview</Title>
          <Text c="dimmed" size="sm">
            Combined read-only snapshot of Trading realized profit and Production stock value.
          </Text>
          <Button component={Link} to="/portfolio" mt="sm" rightSection={<IconArrowRight size={14} />}>Open</Button>
        </Stack>
      </Card>

      <Text size="xs" c="dimmed" ta="center" mt="xl">
        EVE Trader is an unofficial third-party tool, not affiliated with or endorsed by CCP hf.
        EVE, EVE Online, CCP, and all related logos and trademarks are the property of CCP hf.
      </Text>
    </Container>
  )
}
