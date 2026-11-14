// Form edits produce the same bounded replay contract as the advanced JSON input.
type Change = { operation: 'replace'; path: string; content: string; expected_sha256: string }
type Decision = {
  action: 'read_file' | 'apply_changes' | 'check_file' | 'read_receipt' | 'finish' | 'stop'
  check?: 'exists' | 'contains' | 'not_contains' | 'sha256'
  expected?: string
  receipt_id?: string
  receipt_sha256?: string
  path?: string
  changes?: Change[]
}
type Branch = { label: string; decisions: Decision[] }
export default function BranchEditor({
  value,
  onChange,
  files,
  disabled,
  receipts = [],
}: {
  value: string
  onChange: (value: string) => void
  files: { path: string; sha256: string }[]
  receipts?: { id: string; tool: string; sha256: string }[]
  disabled: boolean
}) {
  let branches: Branch[]
  try {
    branches = JSON.parse(value)
    if (
      !Array.isArray(branches) ||
      branches.length !== 2 ||
      branches.some(
        (b) =>
          !Array.isArray(b.decisions) ||
          b.decisions.some(
            (d) =>
              ![
                'read_file',
                'apply_changes',
                'check_file',
                'read_receipt',
                'finish',
                'stop',
              ].includes(d.action),
          ),
      )
    )
      throw new Error()
  } catch {
    return <p>高级内容无法用于表单，请先在高级输入中修正格式。</p>
  }
  const save = (next: Branch[]) => onChange(JSON.stringify(next, null, 2))
  const decision = (
    action: Decision['action'],
    path = files[0]?.path || 'src/App.tsx',
  ): Decision =>
    action === 'check_file'
      ? { action, path, check: 'exists' }
      : action === 'read_receipt'
        ? { action, receipt_id: receipts[0]?.id || '', receipt_sha256: receipts[0]?.sha256 || '' }
        : action === 'apply_changes'
          ? {
              action,
              changes: [
                {
                  operation: 'replace',
                  path,
                  content: '',
                  expected_sha256: files.find((f) => f.path === path)?.sha256 || '',
                },
              ],
            }
          : action === 'read_file'
            ? { action, path }
            : { action }
  return (
    <fieldset disabled={disabled}>
      <legend>编辑两个操作分支</legend>
      <p>
        每个分支独立从绑定源码开始；同一文件多次修改需要新的指纹，请使用高级输入。结束或停止后的步骤不会执行。
      </p>
      {branches.map((branch, b) => (
        <fieldset key={b}>
          <legend>分支 {b + 1}</legend>
          <label>
            分支名称
            <input
              value={branch.label}
              maxLength={80}
              onChange={(e) =>
                save(branches.map((v, i) => (i === b ? { ...v, label: e.target.value } : v)))
              }
            />
          </label>
          {branch.decisions.map((d, index) => {
            const update = (next: Decision) =>
              save(
                branches.map((v, i) =>
                  i === b
                    ? { ...v, decisions: v.decisions.map((old, j) => (j === index ? next : old)) }
                    : v,
                ),
              )
            const simple =
              d.action !== 'apply_changes' ||
              (d.changes?.length === 1 && d.changes[0]?.operation === 'replace')
            return (
              <div className="branch-step" key={index}>
                <label>
                  步骤 {index + 1}
                  <select
                    aria-label={`分支${b + 1}步骤${index + 1}`}
                    value={d.action}
                    onChange={(e) => update(decision(e.target.value as Decision['action']))}
                  >
                    <option value="read_file">查看文件</option>
                    <option value="apply_changes">替换文件内容</option>
                    <option value="check_file">核对源码文本</option>
                    <option value="read_receipt" disabled={!receipts.length}>
                      读取历史回执快照
                    </option>
                    <option value="finish">结束</option>
                    <option value="stop">停止</option>
                  </select>
                </label>
                {simple &&
                  (d.action === 'read_file' ||
                    d.action === 'apply_changes' ||
                    d.action === 'check_file') && (
                    <label>
                      文件
                      <select
                        value={d.path || d.changes?.[0]?.path || ''}
                        onChange={(e) => update(decision(d.action, e.target.value))}
                      >
                        {files.map((f) => (
                          <option value={f.path} key={f.path}>
                            {f.path}
                          </option>
                        ))}
                      </select>
                    </label>
                  )}
                {simple && d.action === 'apply_changes' && (
                  <label>
                    完整替换内容
                    <textarea
                      value={d.changes?.[0]?.content || ''}
                      maxLength={65536}
                      onChange={(e) =>
                        update({ ...d, changes: [{ ...d.changes![0], content: e.target.value }] })
                      }
                    />
                  </label>
                )}
                {d.action === 'check_file' && (
                  <>
                    <label>
                      源码检查方式
                      <select
                        aria-label={`分支${b + 1}检查${index + 1}`}
                        value={d.check || 'exists'}
                        onChange={(e) =>
                          update({
                            ...d,
                            check: e.target.value as Decision['check'],
                            expected: e.target.value === 'exists' ? undefined : '',
                          })
                        }
                      >
                        <option value="exists">文件存在</option>
                        <option value="contains">包含文字</option>
                        <option value="not_contains">不包含文字</option>
                        <option value="sha256">文件指纹一致</option>
                      </select>
                    </label>
                    {d.check !== 'exists' && (
                      <label>
                        预期文字或指纹
                        <input
                          aria-label={`分支${b + 1}预期${index + 1}`}
                          value={d.expected || ''}
                          maxLength={4000}
                          onChange={(e) => update({ ...d, expected: e.target.value })}
                        />
                      </label>
                    )}
                    <small>只核对文本，不代表构建或业务验收。</small>
                  </>
                )}
                {d.action === 'read_receipt' && (
                  <label>
                    冻结回执
                    <select
                      aria-label={`分支${b + 1}回执${index + 1}`}
                      value={d.receipt_id || ''}
                      onChange={(e) =>
                        update({
                          ...d,
                          receipt_id: e.target.value,
                          receipt_sha256:
                            receipts.find((r) => r.id === e.target.value)?.sha256 || '',
                        })
                      }
                    >
                      {receipts.map((r) => (
                        <option key={r.id} value={r.id}>
                          {r.tool} · {r.id}
                        </option>
                      ))}
                    </select>
                    <small>只读取原结果，不再调用工具，也不能视为当前分支已通过。</small>
                  </label>
                )}
                {!simple && <p>此步骤包含多项或特殊修改，请在高级输入中编辑。</p>}
                <button
                  disabled={branch.decisions.length <= 1}
                  onClick={() =>
                    save(
                      branches.map((v, i) =>
                        i === b
                          ? { ...v, decisions: v.decisions.filter((_, j) => j !== index) }
                          : v,
                      ),
                    )
                  }
                >
                  移除步骤 {index + 1}
                </button>
              </div>
            )
          })}
          <button
            disabled={branch.decisions.length >= 32}
            onClick={() =>
              save(
                branches.map((v, i) =>
                  i === b ? { ...v, decisions: [...v.decisions, decision('stop')] } : v,
                ),
              )
            }
          >
            追加步骤
          </button>
        </fieldset>
      ))}
    </fieldset>
  )
}
