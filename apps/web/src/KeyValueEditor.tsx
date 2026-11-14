import { type Pair } from './keyValueModel'
export { pairsToRecord, type Pair } from './keyValueModel'
export default function KeyValueEditor({
  label,
  rows,
  onChange,
  limit = 12,
  disabled = false,
}: {
  label: string
  rows: Pair[]
  onChange: (rows: Pair[]) => void
  limit?: number
  disabled?: boolean
}) {
  return (
    <fieldset disabled={disabled} className="key-value-editor">
      <legend>{label}</legend>
      {rows.map((row, index) => (
        <div className="key-value-row" key={index}>
          <label>
            名称
            <input
              aria-label={`${label}名称${index + 1}`}
              value={row.key}
              maxLength={200}
              onChange={(e) =>
                onChange(rows.map((r, i) => (i === index ? { ...r, key: e.target.value } : r)))
              }
            />
          </label>
          <label>
            值
            <input
              aria-label={`${label}值${index + 1}`}
              value={row.value}
              maxLength={8192}
              onChange={(e) =>
                onChange(rows.map((r, i) => (i === index ? { ...r, value: e.target.value } : r)))
              }
            />
          </label>
          <button type="button" onClick={() => onChange(rows.filter((_, i) => i !== index))}>
            移除第{index + 1}行
          </button>
        </div>
      ))}
      <button
        type="button"
        disabled={rows.length >= limit}
        onClick={() => onChange([...rows, { key: '', value: '' }])}
      >
        添加{label}
      </button>
    </fieldset>
  )
}
