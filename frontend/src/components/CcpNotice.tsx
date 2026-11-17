import { Text } from '@mantine/core'
import type { TextProps } from '@mantine/core'

/**
 * The one CCP copyright / trademark notice, shown on the landing page and on
 * every tool page. Keep the wording here only; README.md carries a copy.
 */
export const CCP_NOTICE_TEXT =
  'EVE Online and the EVE logo are the registered trademarks of CCP hf. All rights are reserved worldwide. ' +
  'All other trademarks are the property of their respective owners. EVE Online, the EVE logo, EVE and all ' +
  'associated logos and designs are the intellectual property of CCP hf. EVE Trader is an unofficial ' +
  'third-party tool, not affiliated with or endorsed by CCP hf.'

export default function CcpNotice(props: TextProps) {
  return (
    <Text size="xs" c="dimmed" data-testid="ccp-notice" {...props}>
      {CCP_NOTICE_TEXT}
    </Text>
  )
}
