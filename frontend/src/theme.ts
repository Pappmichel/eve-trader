import { createTheme, type MantineColorsTuple } from '@mantine/core'

// Ported from the old Streamlit ui_shell.py CSS custom properties - keeps the
// "dark trade terminal" identity instead of Mantine's default blue.
export const COLORS = {
  bg: '#0B0F14',
  surface: '#121922',
  surface2: '#182230',
  border: '#24313F',
  text: '#E6EDF3',
  textDim: '#8593A1',
  accent: '#35D0BA', // profit / go
  warn: '#F2A65A', // caution / skip
  info: '#5B8DEF', // already ordered / neutral
  danger: '#E5595C', // inactive / loss
}

// Mantine wants a 10-shade tuple per color; shade index 5 is what "accent"/
// "danger" etc. resolve to by default (see primaryShade below) - the ramp
// around it doesn't need to be perfect since this app mostly uses fixed
// index-5 usage (buttons, badges), not the full tonal scale.
const accent: MantineColorsTuple = [
  '#e6fbf8', '#c2f4ec', '#9aecdf', '#71e4d2', '#4fdcc7',
  '#35D0BA', '#2bb8a3', '#209f8c', '#158675', '#006d5e',
]
const warn: MantineColorsTuple = [
  '#fdf1e6', '#fbdfc2', '#f8cb9a', '#f5b771', '#f3a752',
  '#F2A65A', '#d98f45', '#c07a37', '#a76529', '#8e501b',
]
const danger: MantineColorsTuple = [
  '#fce8e8', '#f7c9c9', '#f1a7a7', '#ec8686', '#e86c6c',
  '#E5595C', '#cc4a4d', '#b33b3e', '#992c2f', '#801d20',
]
const info: MantineColorsTuple = [
  '#eaf0fd', '#cdddfa', '#adc8f7', '#8db3f4', '#75a2f1',
  '#5B8DEF', '#4c7ad6', '#3d67bd', '#2e54a4', '#1f418b',
]

export const theme = createTheme({
  primaryColor: 'accent',
  primaryShade: 5,
  colors: { accent, warn, danger, info },
  fontFamily: 'Inter, sans-serif',
  fontFamilyMonospace: 'JetBrains Mono, monospace',
  headings: {
    fontFamily: 'Rajdhani, sans-serif',
    fontWeight: '700',
  },
  defaultRadius: 'sm',
  components: {
    Table: {
      defaultProps: { highlightOnHover: true, verticalSpacing: 'xs', fontFamily: 'JetBrains Mono, monospace' },
    },
  },
})
