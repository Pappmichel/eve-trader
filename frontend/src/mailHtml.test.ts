import { describe, expect, it } from 'vitest'

import { sanitizeMailBody } from './mailHtml'

function parse(html: string): HTMLElement {
  const root = document.createElement('div')
  root.innerHTML = sanitizeMailBody(html)
  return root
}

describe('sanitizeMailBody', () => {
  it('keeps plain structure and text', () => {
    const out = sanitizeMailBody('Hello <b>pilot</b><br>fly <i>safe</i>')
    expect(out).toBe('Hello <b>pilot</b><br>fly <i>safe</i>')
  })

  it('removes scripts, event handlers, iframes, images, styles and forms', () => {
    const root = parse(
      '<script>alert(1)</script><img src=x onerror="alert(2)"><iframe src="//evil"></iframe>'
      + '<form action="//evil"><input name=p></form><style>body{display:none}</style>'
      + '<div onclick="alert(3)" style="position:fixed">text</div>',
    )
    expect(root.querySelector('script, img, iframe, form, input, style')).toBeNull()
    const div = root.querySelector('div') as HTMLElement
    expect(div.getAttribute('onclick')).toBeNull()
    expect(div.getAttribute('style')).toBeNull()
    expect(root.textContent).toContain('text')
    expect(root.textContent).not.toContain('alert(1)')
  })

  it('drops non-http(s) links but keeps their text', () => {
    const root = parse(
      '<a href="javascript:alert(1)">js</a> <a href="showinfo:1377//123456">a character</a> '
      + '<a href="data:text/html,<script>alert(1)</script>">data</a> <a href="//evil.example/x">proto-relative</a>',
    )
    for (const a of Array.from(root.querySelectorAll('a'))) expect(a.getAttribute('href')).toBeNull()
    expect(root.textContent).toContain('a character')
    expect(root.textContent).toContain('js')
  })

  it('opens real links in a new tab without leaking the opener', () => {
    const root = parse('<a href="https://www.eveonline.com/">EVE</a>')
    const a = root.querySelector('a') as HTMLAnchorElement
    expect(a.getAttribute('href')).toBe('https://www.eveonline.com/')
    expect(a.getAttribute('target')).toBe('_blank')
    expect(a.getAttribute('rel')).toBe('noopener noreferrer nofollow')
  })

  it('flattens EVE font tags (ARGB colours would break the themes) and keeps the text', () => {
    const root = parse('<font size="12" color="#bfffffff">Fleet at <font color="#ff000000">20:00</font></font>')
    expect(root.querySelector('font')).toBeNull()
    expect(root.innerHTML).not.toContain('color')
    expect(root.textContent).toBe('Fleet at 20:00')
  })

  it('removes unknown EVE tags such as <loc> but keeps their content', () => {
    expect(parse('<loc>Jita</loc> station').textContent).toBe('Jita station')
  })

  it('survives mutation-XSS style payloads', () => {
    const out = sanitizeMailBody(
      '<svg><style><img src=x onerror=alert(1)></style></svg><math><mtext><table><mglyph><style><!--</style><img title="--&gt;&lt;img src=1 onerror=alert(1)&gt;">',
    )
    expect(out.toLowerCase()).not.toContain('onerror')
    expect(out.toLowerCase()).not.toContain('<img')
    expect(out.toLowerCase()).not.toContain('<svg')
  })

  it('returns an empty string for an empty body', () => {
    expect(sanitizeMailBody('')).toBe('')
  })
})
