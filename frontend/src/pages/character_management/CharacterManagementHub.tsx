import { Button, Container, Group, SimpleGrid, Text, Title } from '@mantine/core'
import { IconArrowLeft } from '@tabler/icons-react'
import { Link } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'

import { charSkillsApi, gateApi } from '../../api/client'
import { ToolCard } from '../../components/ToolCard'
import { CHARACTER_MANAGEMENT_TOOL_KEYS, hasAnyToolGrant } from '../../toolKeys'
import { warningSummary } from './skills/queueWarning'

// docs/CHARACTER_MANAGEMENT_PLAN.md. The hub has no grant of its own: it lists
// whichever sub-tools the session holds. Later phases add cards here (Info,
// Skills, Mail, ...) together with their tool_key and router.
export default function CharacterManagementHub() {
  const { data: gateStatus } = useQuery({ queryKey: ['gate', 'status'], queryFn: gateApi.status })
  const tools = gateStatus?.tools
  // The Skills card's badge: how many skill queues need attention. Only asked
  // once the grant is known to be held (never while the gate status loads), and
  // a failure just means no badge.
  const skillsWarnings = useQuery({
    queryKey: ['char-skills', 'warnings'], queryFn: charSkillsApi.warnings,
    enabled: tools !== undefined && tools.includes('char_skills'), retry: false,
  })

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
            badge={warningSummary(skillsWarnings.data?.count ?? 0)}
            description="Skill points, attributes, training queues, every trained skill and a skills matrix across your characters." />
          <ToolCard tools={tools} toolKey="char_mail" to="/character-management/mail" title="Mail"
            description="A mail client for all your characters: read live from ESI, with an optional searchable archive." />
          <ToolCard tools={tools} toolKey="char_notifications" to="/character-management/notifications" title="Notifications"
            description="Structure attacks, war declarations, sovereignty and other in-game notifications across your characters." />
          <ToolCard tools={tools} toolKey="char_contacts" to="/character-management/contacts" title="Contacts & Calendar"
            description="Contacts with standings and labels, and upcoming calendar events, read live from ESI." />
          <ToolCard tools={tools} toolKey="char_skill_plans" to="/character-management/skill-plans" title="Skill Plans"
            description="Build skill plans with prerequisites filled in, import and export them, and see how far each character is." />
          <ToolCard tools={tools} toolKey="char_alerts" to="/character-management/alerts" title="Discord Alerts"
            description="Get a Discord DM when a skill queue is about to run empty or new mail arrives. Every alert is opt-in." />
        </SimpleGrid>
      )}
    </Container>
  )
}
