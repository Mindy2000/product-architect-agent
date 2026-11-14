export type RoleModels = Partial<Record<'planner' | 'developer' | 'reviewer', string>>
export const textModelOptions = [
  { id: 'deepseek-flash', label: 'DeepSeek Flash' },
  { id: 'deepseek-v4-pro', label: 'DeepSeek V4 Pro' },
  { id: 'qwen3.8-flash', label: '千问 3.8 Flash · 北京' },
]
export default function RoleModelPicker({
  value,
  onChange,
  disabled = false,
}: {
  value: RoleModels
  onChange: (value: RoleModels) => void
  disabled?: boolean
}) {
  return (
    <details className="expansion-panel">
      <summary>三个智能体的文字模型</summary>
      <p>
        默认沿用上方模型；可为审查角色选择不同供应商。设置在创建方案时冻结，共用原预算和步数，不自动切换。不同模型不保证消除共同偏差。
      </p>
      {(['planner', 'developer', 'reviewer'] as const).map((role) => (
        <label key={role}>
          {{ planner: '产品规划', developer: '开发', reviewer: '审查测试' }[role]}模型
          <select
            aria-label={`${role}模型`}
            value={value[role] || ''}
            disabled={disabled}
            onChange={(e) => {
              const next = { ...value }
              if (e.target.value) next[role] = e.target.value
              else delete next[role]
              onChange(next)
            }}
          >
            <option value="">沿用本次默认模型</option>
            {textModelOptions.map((m) => (
              <option key={m.id} value={m.id}>
                {m.label}
              </option>
            ))}
          </select>
        </label>
      ))}
      <small>千问文字须在本机设置中单独配置并许可；视觉调用许可不能代替文字许可。</small>
    </details>
  )
}
