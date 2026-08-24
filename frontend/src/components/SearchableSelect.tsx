import { Select } from '@mantine/core'

export interface SearchableSelectOption {
  value: string
  label: string
}

interface Props {
  label: string
  placeholder?: string
  data: SearchableSelectOption[]
  value: string | null
  onChange: (value: string | null) => void
  limit?: number
  w?: number | string
  disabled?: boolean
}

// Thin wrapper over Mantine's Select searchable (the app's established
// typeahead pattern - see trading/PriceHistory.tsx's item picker), used for
// every "pick from a large static/semi-static list, filtered client-side
// while typing" field (item names ~27k rows, solar systems ~8.5k rows,
// structure names small/per-tenant - see hooks/useStaticOptions.ts). Data is
// loaded once by the caller and passed in as a plain array - Mantine's own
// built-in client-side substring filter does the "search while typing" over
// it, no per-keystroke fetch. `limit` caps rendered dropdown rows so a large
// `data` array doesn't render thousands of DOM nodes at once.
export function SearchableSelect({ label, placeholder, data, value, onChange, limit = 50, w, disabled }: Props) {
  return (
    <Select
      label={label}
      placeholder={placeholder}
      data={data}
      value={value}
      onChange={onChange}
      searchable
      limit={limit}
      nothingFoundMessage="No match"
      w={w}
      disabled={disabled}
    />
  )
}
