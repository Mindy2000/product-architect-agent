import { API_BASE as API } from './api'
import { agentNames } from './TeamPanel'
import { statusText, stageText } from './runPresentation'
import type { RunRecord } from './runTypes'

export default function RunHistory({ runs }: { runs: RunRecord[] }) {
  return (
    <div className="history-list">
      {runs.length ? (
        runs.map((item) => (
          <article className="trace-card" key={item.run_id}>
            <header>
              <div>
                <b>{item.product_spec?.product_name ?? item.run_id.slice(0, 8)}</b>
                <small>
                  {statusText[item.status] ?? stageText[item.stage] ?? item.stage} ·{' '}
                  {item.run_id.slice(0, 8)}
                </small>
              </div>
              {item.artifact_paths && (
                <a href={API + '/runs/' + item.run_id + '/artifact/trace'}>下载 Trace JSON</a>
              )}
            </header>
            <div className="trace-timeline">
              {item.trace?.map((event, index) => (
                <div className="trace-entry" key={event.stage + '-' + index}>
                  <span className={'trace-mark ' + event.status}>
                    {event.status === 'completed' || event.status === 'passed' ? '✓' : '·'}
                  </span>
                  <div>
                    <b>{event.summary}</b>
                    <small>
                      {event.agent_id ? `${agentNames[event.agent_id] ?? event.agent_id} · ` : ''}
                      {stageText[event.stage] ?? event.stage}
                      {event.at ? ' · ' + new Date(event.at).toLocaleString('zh-CN') : ''}
                      {event.estimated_cost_cny !== undefined
                        ? ' · 约 ¥' + event.estimated_cost_cny.toFixed(4)
                        : ''}
                    </small>
                  </div>
                </div>
              ))}
            </div>
            {item.artifact_paths && (
              <div className="artifact-links">
                <a href={API + '/runs/' + item.run_id + '/download'}>源码 ZIP</a>
                <a href={API + '/runs/' + item.run_id + '/artifact/report'}>交付报告</a>
                <a href={API + '/runs/' + item.run_id + '/artifact/git_diff'}>Git diff</a>
              </div>
            )}
          </article>
        ))
      ) : (
        <div className="empty-history">运行任务后，这里会显示完整阶段 Trace。</div>
      )}
    </div>
  )
}
