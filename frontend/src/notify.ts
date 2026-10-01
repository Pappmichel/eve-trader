import { useSyncExternalStore } from 'react'
import { notifications } from '@mantine/notifications'

// Single entry point for user-facing toasts. Forwards to Mantine unchanged and
// also records each notification so the bell in the header can list the last
// few after their toast has vanished. Call `notify(...)` instead of
// `notifications.show(...)`; the options are Mantine's own.
export type NotifyOptions = Parameters<typeof notifications.show>[0]

export interface NotificationEntry {
  id: number
  title: string
  message: string
  color: string
  at: number
}

const MAX_ENTRIES = 50
const STORAGE_KEY = 'eve-trader:notifications'

interface StoreState {
  entries: NotificationEntry[] // newest first
  lastReadAt: number
}

function load(): StoreState {
  try {
    const raw = sessionStorage.getItem(STORAGE_KEY)
    if (raw) {
      const parsed = JSON.parse(raw) as StoreState
      if (Array.isArray(parsed.entries) && typeof parsed.lastReadAt === 'number') return parsed
    }
  } catch {
    // storage unavailable or corrupt - start empty, never crash over this
  }
  return { entries: [], lastReadAt: 0 }
}

let state: StoreState = load()
let nextId = state.entries.reduce((max, e) => Math.max(max, e.id), 0) + 1
const listeners = new Set<() => void>()

function commit(next: StoreState) {
  state = next
  try {
    sessionStorage.setItem(STORAGE_KEY, JSON.stringify(state))
  } catch {
    // quota exceeded / private browsing - the list just won't survive a reload
  }
  listeners.forEach((l) => l())
}

function text(value: unknown): string {
  return typeof value === 'string' || typeof value === 'number' ? String(value) : ''
}

export function notify(options: NotifyOptions): string {
  const entry: NotificationEntry = {
    id: nextId++,
    title: text(options.title),
    message: text(options.message),
    color: typeof options.color === 'string' ? options.color : 'accent',
    at: Date.now(),
  }
  commit({ ...state, entries: [entry, ...state.entries].slice(0, MAX_ENTRIES) })
  return notifications.show(options)
}

export function markAllNotificationsRead() {
  commit({ ...state, lastReadAt: Date.now() })
}

export function clearNotifications() {
  commit({ entries: [], lastReadAt: Date.now() })
}

function subscribe(listener: () => void) {
  listeners.add(listener)
  return () => { listeners.delete(listener) }
}

export function useNotificationHistory() {
  const current = useSyncExternalStore(subscribe, () => state)
  const unread = current.entries.filter((e) => e.at > current.lastReadAt).length
  return { entries: current.entries, unread }
}

// Test helper: reset the in-memory store.
export function resetNotificationsForTests() {
  state = { entries: [], lastReadAt: 0 }
  nextId = 1
  listeners.forEach((l) => l())
}
