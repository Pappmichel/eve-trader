/**
 * The keys of `current` whose value differs from `loaded`. A settings save
 * sends only these, so a form opened before another browser saved something
 * cannot write that field back with its stale value. Without a loaded
 * reference every key counts as changed.
 */
export function changedFields<T extends object>(loaded: T | undefined | null, current: T): Partial<T> {
  if (!loaded) return { ...current }
  const out: Partial<T> = {}
  for (const key of Object.keys(current) as (keyof T)[]) {
    if (JSON.stringify(loaded[key]) !== JSON.stringify(current[key])) out[key] = current[key]
  }
  return out
}
