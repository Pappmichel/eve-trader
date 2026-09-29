import { Button, Container, Group, SimpleGrid, Text, Title } from '@mantine/core'
import { IconArrowLeft } from '@tabler/icons-react'
import { Link } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'

import { gateApi } from '../../api/client'
import { ToolCard } from '../../components/ToolCard'
import { CHARACTER_MANAGEMENT_TOOL_KEYS, hasAnyToolGrant } from '../../toolKeys'

// docs/CHARACTER_MANAGEMENT_PLAN.md. The hub has no grant of its own: it lists
// whichever sub-tools the session holds. Later phases add cards here (Info,
// Skills, Mail, ...) together with their tool_key and router.
export default function CharacterManagementHub() {
  const { data: gateStatus } = useQuery({ queryKey: ['gate', 'status'], queryFn: gateApi.status })
  const tools = gateStatus?.tools

  return (
    <Container size="md" py="xl">
      <Group justify="space-between" align="flex-start" mb="lg">
        <div>
          <Text tt="uppercase" size="xs" c="dimmed" fw={600} lts={2}>C-J Import & Manufacturing</Text>
          <Title order={1}>Character Management</Title>
        </div>
        <Button component={Link} to="/" variant="subtle" leftSection={<IconArrowLeft size={14} />}>Back</Button>
      </Group>

      {!hasAnyToolGrant(tools, CHARACTER_MANAGEMENT_TOOL_KEYS) ? (
        <Text c="dimmed">You have no Character Management tools yet. Ask an admin to grant one.</Text>
      ) : (
        <SimpleGrid cols={{ base: 1, xs: 2, sm: 3 }} spacing="md">
          <ToolCard tools={tools} toolKey="characters" to="/character-management/characters" title="Characters"
            description="Who is logged in for ESI data, which tools may read it, and which scopes still need a re-authorize." />
          <ToolCard tools={tools} toolKey="char_info" to="/character-management/info" title="Character Info"
            description="Location, ship, online status, wallet, standings, loyalty points and corporation history for each character." />
          <ToolCard tools={tools} toolKey="char_skills" to="/character-management/skills" title="Skills"
            description="Skill points, attributes, training queues, every trained skill and a skills matrix across your characters." />
        </SimpleGrid>
      )}
    </Container>
  )
}
