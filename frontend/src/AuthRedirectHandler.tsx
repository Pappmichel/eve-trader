import { useEffect } from 'react'
import { useSearchParams } from 'react-router-dom'
import { notify } from './notify'

// The backend redirects here with fixed error codes only. Anything else in
// the URL is attacker-controllable text and must not be shown as is.
export const LOGIN_ERROR_TEXT: Record<string, string> = {
  missing_code_state: 'The login response was incomplete. Try again.',
  sso_error: 'EVE Online reported an error during login. Try again.',
  state_expired_or_unknown: 'The login expired. Start it again.',
  nonce_mismatch: 'The login must be completed in the browser that started it.',
  token_exchange_failed: 'EVE Online did not accept the login. Try again.',
  identity_failed: 'The character could not be verified. Try again.',
  character_mismatch: 'You logged in with a different character than the one being re-authorized.',
  session_lost: 'Your session was lost during login. Log in again.',
  unknown_flow: 'Login failed. Try again.',
  affiliation_unavailable: "Couldn't verify your corporation, try again later.",
}
const DEFAULT_LOGIN_ERROR = 'Login failed. Try again.'

export function loginErrorText(code: string | null): string {
  if (code && Object.prototype.hasOwnProperty.call(LOGIN_ERROR_TEXT, code)) {
    return LOGIN_ERROR_TEXT[code]
  }
  return DEFAULT_LOGIN_ERROR
}

const CHARACTER_NAME = /^[A-Za-z0-9 .'-]{3,37}$/
const ROLE_KEY = /^[a-z-]+:\d+$/

export function AuthRedirectHandler() {
  const [params, setParams] = useSearchParams()

  useEffect(() => {
    const auth = params.get('auth')
    const gate = params.get('gate')
    const rawCharacter = params.get('character')
    const character = rawCharacter && CHARACTER_NAME.test(rawCharacter) ? rawCharacter : null
    if (auth === 'success') {
      const rawRole = params.get('role')
      const role = rawRole && ROLE_KEY.test(rawRole) ? rawRole : null
      const message = [role, character].filter(Boolean).join(': ')
      notify({ title: 'Login successful', message: message || 'Character authorized.', color: 'accent' })
    } else if (auth === 'error') {
      notify({ title: 'Login failed', message: loginErrorText(params.get('message')), color: 'danger' })
    } else if (gate === 'success') {
      notify({
        title: 'Access granted',
        message: character ? `Logged in as ${character}` : 'Logged in.',
        color: 'accent',
      })
    } else if (gate === 'pending') {
      notify({
        title: 'Request submitted',
        message: 'An admin will review it.',
        color: 'warn', autoClose: false,
      })
    } else if (gate === 'rejected') {
      notify({
        title: 'Access rejected',
        message: 'An admin rejected this request. It stays closed until they delete the rejection.',
        color: 'danger', autoClose: false,
      })
    } else if (gate === 'suspended') {
      notify({
        title: 'Access suspended',
        message: 'Your corporation or alliance is no longer allowlisted.',
        color: 'danger', autoClose: false,
      })
    } else if (gate === 'denied') {
      notify({
        title: 'Access denied',
        message: 'Your corporation or alliance is not allowlisted.',
        color: 'danger', autoClose: false,
      })
    } else if (gate === 'error') {
      notify({ title: 'Login failed', message: loginErrorText(params.get('message')), color: 'danger' })
    } else {
      return
    }
    params.delete('auth')
    params.delete('role')
    params.delete('character')
    params.delete('message')
    params.delete('gate')
    setParams(params, { replace: true })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  return null
}
