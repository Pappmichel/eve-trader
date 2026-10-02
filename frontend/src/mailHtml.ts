import DOMPurify from 'dompurify'

// EVE mail bodies are HTML-ish text written by *any player who can send you a
// mail* (<font size color>, <br>, <a href="showinfo:...">, <loc>, ...). They
// are untrusted input: this is the only place a mail body becomes markup, and
// nothing else in the app may put a mail body into the DOM as HTML
// (docs/CHARACTER_MANAGEMENT_PLAN.md phase 3).
//
// Deliberately strict: a small whitelist of structural tags, links only with a
// real http(s) URL, no styling at all. EVE's <font color="#bfffffff"> is
// ARGB and would clash with the app's dark/light themes (near-black text on a
// dark background is unreadable), so colours and sizes are dropped and the
// text takes the theme colour. Unknown tags (<loc>, <img>, <script>, ...) are
// removed and their text content kept; showinfo:/killreport: links lose their
// href (no in-game link target here) but keep their text.
const ALLOWED_TAGS = [
  'a', 'b', 'strong', 'i', 'em', 'u', 'br', 'p', 'div', 'span', 'ul', 'ol', 'li',
  'blockquote', 'hr', 'small', 'sub', 'sup',
]

const purifier = DOMPurify(window)
purifier.addHook('afterSanitizeAttributes', (node: Element) => {
  if (node.tagName !== 'A') return
  const href = node.getAttribute('href') ?? ''
  if (/^https?:\/\//i.test(href)) {
    node.setAttribute('target', '_blank')
    node.setAttribute('rel', 'noopener noreferrer nofollow')
  } else {
    node.removeAttribute('href')
  }
})

export function sanitizeMailBody(html: string): string {
  return purifier.sanitize(html, {
    ALLOWED_TAGS,
    ALLOWED_ATTR: ['href'],
    ALLOWED_URI_REGEXP: /^https?:\/\//i,
    KEEP_CONTENT: true,
    RETURN_TRUSTED_TYPE: false,
  }) as string
}
