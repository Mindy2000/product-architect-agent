import { useEffect, useState } from 'react'
import { apiJson } from './api'

type Meaning = { id: string; title: string; meaning: string }
type Contract = { revision: number; levels: Meaning[]; boundaries: Meaning[] }

export default function EvidenceGuide() {
  const [contract, setContract] = useState<Contract | null>(null)
  const [error, setError] = useState('')
  useEffect(() => {
    let gone = false
    apiJson<Contract>('/workbench/evidence-policy')
      .then((value) => {
        if (!gone) setContract(value)
      })
      .catch(() => {
        if (!gone) setError('能力说明暂不可用；执行完成不代表需求验收通过。')
      })
    return () => {
      gone = true
    }
  }, [])
  return (
    <details className="expansion-panel evidence-guide">
      <summary>如何理解检查结果与当前能力</summary>
      <p>执行完成、技术检查和逐条需求确认分别记录。请按证据来源判断结果。</p>
      {error && <p role="status">{error}</p>}
      {contract && (
        <>
          <h3>证据等级说明</h3>
          <dl>
            {contract.levels.map((item) => (
              <div key={item.id}>
                <dt>
                  <b>{item.title}</b>
                </dt>
                <dd>{item.meaning}</dd>
              </div>
            ))}
          </dl>
          <h3>当前能力边界</h3>
          <dl>
            {contract.boundaries.map((item) => (
              <div key={item.id}>
                <dt>
                  <b>{item.title}</b>
                </dt>
                <dd>{item.meaning}</dd>
              </div>
            ))}
          </dl>
        </>
      )}
    </details>
  )
}
