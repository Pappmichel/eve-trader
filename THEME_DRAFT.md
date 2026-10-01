# Theme draft (not integrated, not committed)

Goal: make the existing Mantine "trade terminal" look more cohesive without
changing the stack. Two files are touched: `frontend/src/theme.ts` (component
overrides) and `frontend/src/index.css` (panel decoration). Nothing here
changes the colour palette in `COLORS`.

Verify before merging: load the Landing, Trading shortlist and Production
pages in a browser (Playwright script, then delete it), check the console, and
run `npm run build`, `npm run lint`, `npm test` in `frontend/`.

## 1. `theme.ts` - add to `createTheme({ ... components })`

```ts
components: {
  Table: {
    defaultProps: { highlightOnHover: true, verticalSpacing: 'xs', fontFamily: 'JetBrains Mono, monospace' },
    styles: {
      th: {
        color: COLORS.textDim,
        fontFamily: 'Rajdhani, sans-serif',
        fontSize: 13,
        letterSpacing: '0.06em',
        textTransform: 'uppercase',
        borderBottom: `1px solid ${COLORS.border}`,
        background: COLORS.surface,
      },
      td: { borderBottom: `1px solid ${COLORS.border}` },
    },
  },

  Paper: {
    defaultProps: { radius: 'sm', withBorder: true },
    styles: { root: { background: COLORS.surface, borderColor: COLORS.border } },
  },

  Card: {
    defaultProps: { radius: 'sm', withBorder: true, padding: 'md' },
    styles: {
      root: {
        background: `linear-gradient(180deg, ${COLORS.surface2} 0%, ${COLORS.surface} 100%)`,
        borderColor: COLORS.border,
        transition: 'border-color 120ms ease, transform 120ms ease',
        '&:hover': { borderColor: COLORS.accent },
      },
    },
  },

  Badge: {
    defaultProps: { radius: 'xs', variant: 'light' },
    styles: { root: { fontFamily: 'Rajdhani, sans-serif', letterSpacing: '0.05em', fontWeight: 700 } },
  },

  Tabs: {
    styles: {
      tab: {
        fontFamily: 'Rajdhani, sans-serif',
        letterSpacing: '0.04em',
        color: COLORS.textDim,
        '&[data-active]': { color: COLORS.accent, borderColor: COLORS.accent },
      },
      list: { borderColor: COLORS.border },
    },
  },

  NavLink: {
    styles: {
      root: { borderRadius: 4, '&[data-active]': { background: 'rgba(53, 208, 186, 0.10)', color: COLORS.accent } },
      label: { fontFamily: 'Rajdhani, sans-serif', letterSpacing: '0.03em' },
    },
  },

  Button: { defaultProps: { radius: 'sm' }, styles: { root: { fontFamily: 'Rajdhani, sans-serif', letterSpacing: '0.05em', fontWeight: 700 } } },

  TextInput: { styles: { input: { background: COLORS.bg, borderColor: COLORS.border } } },
  NumberInput: { styles: { input: { background: COLORS.bg, borderColor: COLORS.border } } },
  Select: { styles: { input: { background: COLORS.bg, borderColor: COLORS.border } } },
},
```

Note: Mantine 9 may prefer `classNames` + CSS modules over nested `'&:hover'`
in `styles` (the latter only works through its emotion-free style API in some
versions). If `&:hover`/`&[data-active]` do not apply, move those two rules to
`index.css` using the `.mantine-Card-root:hover` / `[data-active]` selectors.

## 2. `index.css` - panel decoration (opt-in via a class)

```css
:root {
  --et-accent: #35D0BA;
  --et-border: #24313F;
  --et-surface: #121922;
}

/* Use on a hero panel or section header: className="et-panel" */
.et-panel {
  position: relative;
  background: linear-gradient(180deg, #182230 0%, var(--et-surface) 100%);
  border: 1px solid var(--et-border);
  border-radius: 4px;
}
.et-panel::before,
.et-panel::after {
  content: '';
  position: absolute;
  width: 10px;
  height: 10px;
  border: 1px solid var(--et-accent);
  opacity: 0.7;
  pointer-events: none;
}
.et-panel::before { top: -1px; left: -1px; border-right: 0; border-bottom: 0; }
.et-panel::after { bottom: -1px; right: -1px; border-left: 0; border-top: 0; }

/* Subtle scrollbars that match the surface colours */
* { scrollbar-width: thin; scrollbar-color: #24313F #0B0F14; }

/* Tabular numbers so price columns align */
table td { font-variant-numeric: tabular-nums; }
```

## 3. Rollout order (smallest first)

1. Table + Badge + Button overrides (visible everywhere, lowest risk).
2. Tabs + NavLink (navigation feel).
3. Card/Paper gradients and hover.
4. `.et-panel` corner accents on the Landing cards and page headers only.

## 4. Deliberately not included

- No new fonts (Inter / Rajdhani / JetBrains Mono already in use).
- No palette change, no light mode.
- No animation beyond the 120 ms border transition (data-dense tool).
