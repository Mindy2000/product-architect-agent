import { useState } from 'react'
export default function VisualSettings({
  busy,
  apply,
}: {
  busy: boolean
  apply: (settings: object) => Promise<unknown>
}) {
  const [enabled, setEnabled] = useState(false),
    [viewport, setViewport] = useState('desktop'),
    [storage, setStorage] = useState('{}'),
    [api, setApi] = useState('{}'),
    [masks, setMasks] = useState('[]'),
    [region, setRegion] = useState('null'),
    [error, setError] = useState('')
  return (
    <details className="expansion-panel">
      <summary>视觉对照设置</summary>
      <p>
        独立模式使用合成浏览器数据、固定时间、关闭动画；业务接口仅使用下方合成响应。不会覆盖原预览数据。基线与比较必须使用相同设置。
      </p>
      <label>
        <input type="checkbox" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} />
        开启独立视觉模式
      </label>
      <label>
        视口
        <select aria-label="视口" value={viewport} onChange={(e) => setViewport(e.target.value)}>
          <option value="desktop">桌面 1000 × 720</option>
          <option value="mobile">手机 390 × 844</option>
        </select>
      </label>
      {enabled && (
        <>
          <label>
            合成 localStorage（键值 JSON）
            <textarea
              value={storage}
              onChange={(e) => setStorage(e.target.value)}
              maxLength={16384}
            />
          </label>
          <label>
            业务接口样本（路径 → JSON 字符串；未配置返回 404）
            <textarea value={api} onChange={(e) => setApi(e.target.value)} maxLength={16384} />
          </label>
          <label>
            动态区域遮罩（矩形数组，例如 [{'{"x":0,"y":0,"width":100,"height":30}'}]）
            <textarea value={masks} onChange={(e) => setMasks(e.target.value)} maxLength={2000} />
          </label>
          <label>
            只比较的区域（同样格式的单个矩形；null 比较全屏）
            <textarea value={region} onChange={(e) => setRegion(e.target.value)} maxLength={300} />
          </label>
        </>
      )}
      <button
        disabled={busy}
        onClick={async () => {
          try {
            setError('')
            await apply({
              enabled,
              viewport,
              storage: enabled ? JSON.parse(storage) : {},
              api_fixtures: enabled ? JSON.parse(api) : {},
              masks: enabled ? JSON.parse(masks) : [],
              region: enabled ? JSON.parse(region) : null,
            })
          } catch {
            setError('请检查 JSON 格式及区域范围。')
          }
        }}
      >
        应用设置并刷新截图
      </button>
      {error && <p className="inline-state error">{error}</p>}
    </details>
  )
}
