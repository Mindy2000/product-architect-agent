export type Pair = { key: string; value: string }
export function pairsToRecord(rows: Pair[]) {
  const result: Record<string, string> = Object.create(null)
  for (const row of rows) {
    if (!row.key.trim() && !row.value) continue
    const key = row.key.trim()
    if (!key) throw new Error('请填写每一行的名称，或移除空行。')
    if (Object.hasOwn(result, key)) throw new Error('名称不能重复：' + key)
    result[key] = row.value
  }
  return result
}
