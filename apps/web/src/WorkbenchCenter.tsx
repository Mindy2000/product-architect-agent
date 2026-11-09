import EvaluationCenter from './EvaluationCenter'
import { API_BASE, apiFetch } from './api'
import { useEffect, useState } from 'react'
import MaintenanceJob, { MaintenanceHistory, startLocalJob, type LocalJob } from './MaintenanceJob'
const API = API_BASE + '/workbench'
type Check = { id: string; title: string; ready: boolean; required: boolean; help: string }
type Setup = {
  checks: Check[]
  ready: boolean
  completed: boolean
  budget_cny: number
  disk_text_enabled: boolean
  restart_required: boolean
}
type Plan = {
  file_count: number
  bytes: number
  groups: { name: string; files: number; bytes: number }[]
  excluded: string[]
  warning: string
}
type Preview = {
  token: string
  file_count: number
  bytes: number
  version: string
  message: string
  archive_sha256?: string
  compatibility?: {
    source_version: string
    target_version: string
    migration_required: boolean
    steps: string[]
  }
}
async function request(path: string, init?: RequestInit) {
  const response = await apiFetch(API + path, init)
  if (!response.ok) {
    const data = await response.json().catch(() => null)
    throw new Error(typeof data?.detail === 'string' ? data.detail : '本地操作未完成，请稍后重试。')
  }
  return response
}
async function json(path: string, body?: object) {
  return (
    await request(
      path,
      body
        ? {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
          }
        : undefined,
    )
  ).json()
}
function size(bytes: number) {
  return (bytes / 1024 / 1024).toFixed(2) + ' MiB'
}
async function download(response: Response, name: string) {
  const url = URL.createObjectURL(await response.blob())
  const link = document.createElement('a')
  link.href = url
  link.download = name
  link.click()
  window.setTimeout(() => URL.revokeObjectURL(url), 30000)
}
const groupNames: Record<string, string> = {
  'data/runs.sqlite3': '项目、产品方案与直接参考资料',
  'data/checkpoints.sqlite3': '运行检查点',
  'data/tools.sqlite3': '工具与执行记录',
  'data/api_usage.sqlite3': '费用记录与图片登记',
  'data/operations.sqlite3': '本地操作审计',
  'data/projects': '项目清单与维护信息',
  'data/git': '源码版本历史',
  'data/applications': '业务数据库与附件',
  'data/knowledge': '本地知识库与原始文档',
  'data/dify': 'Dify本地绑定与额度记录',
  'data/research': '网页来源与快照',
  'data/vision': '用户上传的图片',
  'data/workbench': '工作台偏好',
  'generated-projects': '生成项目源码',
  outputs: '项目交付文件',
}

function SetupWizard({ onBack }: { onBack: () => void }) {
  const [status, setStatus] = useState<Setup | null>(null),
    [provider, setProvider] = useState('deepseek'),
    [key, setKey] = useState(''),
    [enabled, setEnabled] = useState(false),
    [budget, setBudget] = useState(99),
    [busy, setBusy] = useState(false),
    [error, setError] = useState(''),
    [notice, setNotice] = useState('')
  async function load() {
    setBusy(true)
    setError('')
    try {
      const next = (await json('/setup')) as Setup
      setStatus(next)
      setEnabled(next.disk_text_enabled)
      setBudget(next.budget_cny)
    } catch (e) {
      setError(e instanceof Error ? e.message : '检查失败')
    } finally {
      setBusy(false)
    }
  }
  useEffect(() => {
    void load()
  }, [])
  async function save() {
    setBusy(true)
    setError('')
    setNotice('')
    try {
      const result = await json('/setup/config', {
        provider,
        api_key: key,
        ...(provider === 'deepseek' ? { enabled, budget_cny: budget } : {}),
      })
      setKey('')
      await load()
      setNotice(result.message)
    } catch (e) {
      setError(e instanceof Error ? e.message : '保存失败')
    } finally {
      setBusy(false)
    }
  }
  async function complete() {
    setBusy(true)
    setError('')
    try {
      await json('/setup/complete', {})
      localStorage.setItem('pa-onboarding-dismissed', 'true')
      setNotice('首次使用准备已完成，没有发起模型请求。')
      await load()
    } catch (e) {
      setError(e instanceof Error ? e.message : '未完成')
    } finally {
      setBusy(false)
    }
  }
  return (
    <section className="workbench-card" aria-labelledby="setup-title">
      <div className="workbench-heading">
        <div>
          <span className="workbench-kicker">开始使用</span>
          <h2 id="setup-title">首次使用向导</h2>
          <p>检查本地环境、填写自己的密钥并设置预算。检查过程不连接模型供应商。</p>
        </div>
        <button disabled={busy} onClick={() => void load()}>
          重新检查
        </button>
      </div>
      <ol className="setup-checks">
        {status?.checks.map((item) => (
          <li key={item.id} className={item.ready ? 'check-ready' : 'check-pending'}>
            <span className="check-symbol">{item.ready ? '✓' : '○'}</span>
            <div>
              <b>{item.title}</b>
              <small>
                {item.required ? '必要配置' : '按需配置'} · {item.ready ? '已就绪' : '待准备'}
              </small>
              <p>{item.help}</p>
            </div>
          </li>
        ))}
      </ol>
      <fieldset disabled={busy} className="setup-form">
        <legend>配置自己的服务</legend>
        <label>
          配置服务
          <select
            aria-label="向导配置服务"
            value={provider}
            onChange={(e) => {
              setProvider(e.target.value)
              setKey('')
              setNotice('')
            }}
          >
            <option value="deepseek">DeepSeek · 文字生成</option>
            <option value="qwen">千问 · 可选视觉理解</option>
            <option value="dify">Dify · 可选知识库</option>
          </select>
        </label>
        <label>
          API Key（留空保留已有密钥）
          <input
            aria-label="向导API密钥"
            type="password"
            autoComplete="off"
            value={key}
            onChange={(e) => setKey(e.target.value)}
            maxLength={1024}
          />
        </label>
        {provider === 'deepseek' && (
          <>
            <label>
              累计费用上限（人民币，0.25–99 元）
              <input
                aria-label="向导累计费用上限"
                type="number"
                min="0.25"
                max="99"
                step="0.25"
                value={budget}
                onChange={(e) => setBudget(Number(e.target.value))}
              />
            </label>
            <label className="workbench-checkbox">
              <input
                type="checkbox"
                checked={enabled}
                onChange={(e) => setEnabled(e.target.checked)}
              />
              我允许文字模型产生费用，受单任务和累计预算共同限制
            </label>
          </>
        )}
        <p>
          密钥只写入本机
          .env，不回显或导出。保存后需要停止并重新启动工作台。可选服务在这里仅保存密钥，不自动调用、上传或检索。
        </p>
        <button
          className="primary"
          disabled={busy || (provider !== 'deepseek' && !key) || !Number.isFinite(budget)}
          onClick={() => void save()}
        >
          保存本地配置
        </button>
      </fieldset>
      {status?.restart_required && (
        <p role="status" className="inline-state">
          本地配置与当前运行状态不同，请停止并重新启动工作台，再回来重新检查。
        </p>
      )}
      {notice && (
        <p role="status" className="inline-state">
          {notice}
        </p>
      )}
      {error && (
        <p role="alert" className="inline-state error">
          {error}
        </p>
      )}
      <div className="workbench-actions">
        <button
          disabled={busy || !status?.ready || status.restart_required}
          onClick={() => void complete()}
        >
          {status?.completed ? '更新准备完成记录' : '标记准备完成'}
        </button>
        <button
          onClick={() => {
            localStorage.setItem('pa-onboarding-dismissed', 'true')
            onBack()
          }}
        >
          返回开发工作台
        </button>
      </div>
    </section>
  )
}
function WorkbenchBackup() {
  const [jobId, setJobId] = useState('')
  function finished(job: LocalJob) {
    void loadUploads()
    if (job.status === 'completed' && job.result.location) setLocation(job.result.location)
  }
  const [uploads, setUploads] = useState<{ token: string; bytes: number; expired: boolean }[]>([])
  async function loadUploads() {
    try {
      setUploads((await json('/backups/previews')).items)
    } catch {
      /* Main operation reports actionable errors. */
    }
  }
  useEffect(() => {
    void loadUploads()
  }, [])
  const [plan, setPlan] = useState<Plan | null>(null),
    [preview, setPreview] = useState<Preview | null>(null),
    [confirmed, setConfirmed] = useState(false),
    [busy, setBusy] = useState(false),
    [error, setError] = useState(''),
    [notice, setNotice] = useState(''),
    [location, setLocation] = useState('')
  async function run(action: () => Promise<void>, refresh = true) {
    setBusy(true)
    setError('')
    setNotice('')
    try {
      await action()
    } catch (e) {
      setError(e instanceof Error ? e.message : '操作未完成')
    } finally {
      setBusy(false)
      if (refresh) void loadUploads()
    }
  }
  async function select(file: File) {
    setPreview(null)
    setConfirmed(false)
    setLocation('')
    await run(async () => {
      if (file.size > 512 * 1024 * 1024) throw new Error('工作台备份不能超过512MiB。')
      setPreview(await (await request('/backups/preview', { method: 'POST', body: file })).json())
    })
  }
  async function discard() {
    if (!preview) return
    await run(async () => {
      await request('/backups/preview/' + preview.token, { method: 'DELETE' })
      setPreview(null)
      setConfirmed(false)
      setNotice('上传的预览副本已移除，原备份文件保留。')
    })
  }
  return (
    <section className="workbench-card" aria-labelledby="backup-title">
      <div className="workbench-heading">
        <div>
          <span className="workbench-kicker">保留你的工作</span>
          <h2 id="backup-title">整套工作台备份与恢复</h2>
          <p>
            包含全部项目、方案、检查点、本地知识库、业务数据、附件和费用记录。Dify只备份本地绑定与额度记录，云端知识库仍由平台托管。
          </p>
        </div>
      </div>
      <p className="inline-state">
        备份包含文档和业务资料，请私下保管；用户资料正文原样保留。不包含 .env、API
        密钥配置、依赖和临时缓存，不会自动上传。
      </p>
      <button
        disabled={busy}
        onClick={() => void run(async () => setPlan(await json('/backups/plan')))}
      >
        预览工作台备份范围
      </button>
      {plan && (
        <>
          <p>
            {plan.file_count} 个文件，原始大小约 {size(plan.bytes)}
            。备份前请暂停开发、等待资料处理完成并关闭交互预览。
          </p>
          <ul className="backup-groups">
            {plan.groups.map((g) => (
              <li key={g.name}>
                <span>{groupNames[g.name] ?? g.name}</span>
                <small>
                  {g.files} 文件 · {size(g.bytes)}
                </small>
              </li>
            ))}
          </ul>
          <details>
            <summary>排除内容</summary>
            <ul>
              {plan.excluded.map((text) => (
                <li key={text}>{text}</li>
              ))}
            </ul>
          </details>
          <button
            className="primary"
            disabled={busy}
            onClick={() =>
              void run(async () => {
                const job = await startLocalJob({ kind: 'backup' })
                setJobId(job.id)
                setNotice('备份已进入本机维护队列，完成后可下载。')
              }, false)
            }
          >
            下载整套工作台备份
          </button>
        </>
      )}
      <div className="workbench-divider" />
      <h3>恢复为独立工作台</h3>
      <p>选择备份后先校验格式、版本和每个文件的哈希。恢复不会覆盖当前工作台，也不会启动任务。</p>
      <label>
        选择工作台备份 ZIP
        <input
          aria-label="选择工作台备份ZIP"
          type="file"
          accept=".zip"
          disabled={busy}
          onChange={(e) => {
            const file = e.target.files?.[0]
            if (file) void select(file)
            e.target.value = ''
          }}
        />
      </label>
      {uploads.length > 0 && (
        <details>
          <summary>本机暂存的备份预览（{uploads.length}）</summary>
          <p>只管理上传到本机的暂存副本，不会删除你下载的原备份文件。</p>
          {uploads.map((item) => (
            <div className="pending-backup" key={item.token}>
              <span>
                {item.token.slice(0, 8)} · {size(item.bytes)} · {item.expired ? '已过期' : '可校验'}
              </span>
              <button
                disabled={busy || item.expired}
                onClick={() =>
                  void run(async () => {
                    setPreview(await json('/backups/preview/' + item.token))
                    setConfirmed(false)
                  })
                }
              >
                重新校验
              </button>
              <button
                disabled={busy}
                onClick={() =>
                  void run(async () => {
                    await request('/backups/preview/' + item.token, { method: 'DELETE' })
                    if (preview?.token === item.token) {
                      setPreview(null)
                      setConfirmed(false)
                    }
                  })
                }
              >
                移除此暂存
              </button>
            </div>
          ))}
        </details>
      )}
      {preview && (
        <div className="restore-preview">
          <p>{preview.message}</p>
          {preview.compatibility?.migration_required && (
            <div className="migration-preview">
              <b>
                兼容升级：{preview.compatibility.source_version} →{' '}
                {preview.compatibility.target_version}
              </b>
              <ul>
                {preview.compatibility.steps.map((step) => (
                  <li key={step}>{step}</li>
                ))}
              </ul>
              <p>失败只清理本次新副本；原始迁移前备份另存于恢复副本中，不能直接降级覆盖。</p>
            </div>
          )}
          <p>
            版本 {preview.version} · {preview.file_count} 文件 · {size(preview.bytes)}
          </p>
          <label className="workbench-checkbox">
            <input
              type="checkbox"
              checked={confirmed}
              disabled={busy}
              onChange={(e) => setConfirmed(e.target.checked)}
            />
            确认创建独立副本，保留当前工作台；新副本密钥为空、模型调用关闭
          </label>
          <div className="workbench-actions">
            <button
              className="primary"
              disabled={busy || !confirmed}
              onClick={() =>
                void run(async () => {
                  const job = await startLocalJob({
                    kind: 'restore',
                    token: preview.token,
                    confirmed: true,
                    expected_sha256: preview.archive_sha256,
                  })
                  setJobId(job.id)
                  setNotice('恢复已进入本机维护队列；当前工作台保留。')
                }, false)
              }
            >
              确认恢复整套工作台
            </button>
            <button disabled={busy} onClick={() => void discard()}>
              移除上传的预览副本
            </button>
          </div>
        </div>
      )}
      {location && !jobId && (
        <div className="restore-location">
          <b>恢复目录</b>
          <code>{location}</code>
          <p>
            先停止当前工作台，在上述目录运行安装入口，再配置密钥、启动。原目录和当前数据保留；恢复副本也未执行任何生成代码。
          </p>
        </div>
      )}
      {jobId && <MaintenanceJob id={jobId} onFinished={finished} />}{' '}
      {busy && <p role="status">正在本机处理，请保持页面打开…</p>}
      {notice && (
        <p role="status" className="inline-state">
          {notice}
        </p>
      )}
      {error && (
        <p role="alert" className="inline-state error">
          {error}
        </p>
      )}
    </section>
  )
}
function Diagnostics() {
  const [snapshot, setSnapshot] = useState<{
      token: string
      report: Record<string, unknown>
    } | null>(null),
    [busy, setBusy] = useState(false),
    [error, setError] = useState('')
  async function preview() {
    setBusy(true)
    setError('')
    setSnapshot(null)
    try {
      setSnapshot(await json('/diagnostics/preview', {}))
    } catch (e) {
      setError(e instanceof Error ? e.message : '诊断未完成')
    } finally {
      setBusy(false)
    }
  }
  return (
    <section className="workbench-card" aria-labelledby="diagnostics-title">
      <div className="workbench-heading">
        <div>
          <span className="workbench-kicker">定位问题</span>
          <h2 id="diagnostics-title">集中脱敏诊断</h2>
          <p>
            先看内容，再手动下载。只收集系统与依赖版本、配置状态和任务状态计数，不读取原始日志、文档、源码或业务数据库正文。
          </p>
        </div>
      </div>
      <button disabled={busy} onClick={() => void preview()}>
        {busy ? '正在准备…' : '预览脱敏诊断内容'}
      </button>
      {snapshot && (
        <>
          <p>
            以下内容就是将导出的诊断快照。不会自动发送给GitHub或任何其他平台；预览10分钟后过期。
          </p>
          <pre className="diagnostic-preview" aria-label="诊断导出预览">
            {JSON.stringify(snapshot.report, null, 2)}
          </pre>
          <a
            className="workbench-download"
            href={API + '/diagnostics/' + snapshot.token + '/download'}
            download
          >
            下载已预览的诊断 ZIP
          </a>
        </>
      )}
      {error && (
        <p role="alert" className="inline-state error">
          {error}
        </p>
      )}
    </section>
  )
}
export default function WorkbenchCenter({ onBack }: { onBack: () => void }) {
  return (
    <div className="workbench-center">
      <div className="workbench-intro">
        <h1>工作台设置与维护</h1>
        <p>本机配置、完整备份和问题诊断都在这里。操作不会自动调用模型。</p>
      </div>
      <SetupWizard onBack={onBack} />
      <WorkbenchBackup />
      <Diagnostics />
      <EvaluationCenter />
      <MaintenanceHistory />
    </div>
  )
}
