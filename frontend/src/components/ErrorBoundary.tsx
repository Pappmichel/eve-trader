import { Component, type ErrorInfo, type ReactNode } from 'react'
import { Box, Button, Container, Group, Stack, Text, Title } from '@mantine/core'
import { COLORS } from '../theme'

// React error boundaries must be class components - there is still no hook
// equivalent (getDerivedStateFromError/componentDidCatch have no functional
// counterpart as of React 19). Without this, an unhandled render error
// anywhere in the tree unmounts the whole app to a blank white page with no
// way back short of a manual URL edit + reload.
interface Props {
  children: ReactNode
}

interface State {
  error: Error | null
}

export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null }

  static getDerivedStateFromError(error: Error): State {
    return { error }
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    // No backend log-shipping for frontend errors exists (out of scope here) -
    // at minimum this must not vanish silently in production, where there's
    // no browser dev tools tab open to catch a plain console.error.
    console.error('Unhandled render error:', error, info.componentStack)
  }

  render() {
    if (!this.state.error) {
      return this.props.children
    }
    return (
      <Container size="sm" py="xl">
        <Stack gap="md">
          <Title order={2}>Something went wrong</Title>
          <Box
            style={{
              borderLeft: `3px solid ${COLORS.danger}`,
              background: COLORS.surface2,
              padding: '0.85rem 1.1rem',
              borderRadius: 4,
            }}
          >
            <Text size="sm" c="dimmed">
              This page hit an unexpected error and couldn't render. Nothing was saved or lost - the rest of
              the app (and your data) is unaffected. Reloading usually clears it; if it keeps happening on the
              same page, that's worth reporting.
            </Text>
          </Box>
          <Text size="xs" c="dimmed" ff="monospace">
            {this.state.error.message}
          </Text>
          <Group>
            <Button onClick={() => window.location.assign('/')}>Go to Landing</Button>
            <Button variant="outline" onClick={() => window.location.reload()}>Reload page</Button>
          </Group>
        </Stack>
      </Container>
    )
  }
}
