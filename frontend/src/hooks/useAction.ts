import { useMutation, useQueryClient } from '@tanstack/react-query'
import { notifications } from '@mantine/notifications'
import { ApiError } from '../api/client'

// Success toasts summarize small/flat results (e.g. {"added": 3, "removed": 1}
// from a Refresh/Sync action) inline, since there's often no other UI showing
// that number - but a large/nested result (a material tree, a full candidate
// list) already renders in the page itself right below, so dumping its raw
// JSON into the toast is just noise; past this length it's cut for a plain
// "Done" instead.
const MAX_RESULT_MESSAGE_LENGTH = 200

// Error messages are the opposite case: unlike a success result, the message
// itself (e.g. a backend ConfigError - "structure_sell_haircut: 5.0 is above
// the maximum allowed value (1)") IS the useful content, so collapsing to a
// generic placeholder the way the success path does would throw away exactly
// what the user needs to see. Truncated with an ellipsis instead - keeps the
// (usually most informative) start of the message, just bounds how much a
// single Mantine notification (sized for a line or two of text) has to hold.
const MAX_ERROR_MESSAGE_LENGTH = 300

function truncate(message: string, max: number): string {
  return message.length > max ? `${message.slice(0, max)}…` : message
}

// Runs a mutation, shows a success/error notification, and invalidates the
// given query keys so dependent views refetch.
export function useAction<TArgs = void, TResult = unknown>(
  label: string,
  fn: (args: TArgs) => Promise<TResult>,
  invalidateKeys: string[][] = [],
) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: fn,
    onSuccess: (result) => {
      const summary = typeof result === 'object' ? JSON.stringify(result) : String(result ?? 'OK')
      notifications.show({
        title: label,
        message: summary.length > MAX_RESULT_MESSAGE_LENGTH ? 'Done' : summary,
        color: 'accent',
      })
      for (const key of invalidateKeys) {
        queryClient.invalidateQueries({ queryKey: key })
      }
    },
    onError: (err: unknown) => {
      const message = err instanceof ApiError ? err.message : String(err)
      notifications.show({ title: `${label} - Error`, message: truncate(message, MAX_ERROR_MESSAGE_LENGTH), color: 'danger' })
    },
  })
}
