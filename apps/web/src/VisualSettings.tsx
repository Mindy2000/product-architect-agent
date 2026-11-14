import { useRef, useState } from 'react'
import KeyValueEditor, { pairsToRecord, type Pair } from './KeyValueEditor'
import { selectionRect, type Rect } from './visualGeometry'
export default function VisualSettings({
  busy,
  apply,
  frame,
}: {
  busy: boolean
  apply: (settings: object) => Promise<unknown>
  frame: {
    image: string
    width: number
    height: number
    visual_settings?: { enabled: boolean; viewport: string }
  }
}) {
  const [enabled, setEnabled] = useState(false),
    [viewport, setViewport] = useState('desktop')
  const [storage, setStorage] = useState<Pair[]>([]),
    [api, setApi] = useState('{}')
  const [masks, setMasks] = useState<Rect[]>([]),
    [region, setRegion] = useState<Rect | null>(null)
  const [mode, setMode] = useState<'mask' | 'region'>('mask'),
    [error, setError] = useState(''),
    [notice, setNotice] = useState('')
  const [draft, setDraft] = useState<Rect | null>(null)
  const origin = useRef<{ x: number; y: number } | null>(null)
  const width = viewport === 'mobile' ? 390 : 1000,
    height = viewport === 'mobile' ? 844 : 720
  const canPick =
    enabled &&
    !busy &&
    frame.width === width &&
    frame.height === height &&
    frame.visual_settings?.enabled &&
    frame.visual_settings.viewport === viewport
  const overlays = [
    ...masks.map((r, i) => ({ r, label: `遮罩${i + 1}`, kind: 'mask' })),
    ...(region ? [{ r: region, label: '比较区域', kind: 'region' }] : []),
    ...(draft ? [{ r: draft, label: '选择中', kind: mode }] : []),
  ]
  function add(rect: Rect) {
    if (mode === 'region') setRegion(rect)
    else if (masks.length < 12) setMasks([...masks, rect])
    else setError('最多12个遮罩，请先移除不需要的区域。')
  }
  return (
    <details className="expansion-panel">
      <summary>视觉对照设置</summary>
      <p>
        独立模式使用合成数据、固定时间和关闭动画。先应用视口和数据，再在截图上拖选区域；基线与比较须使用相同设置。
      </p>
      <label>
        <input type="checkbox" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} />
        开启独立视觉模式
      </label>
      <label>
        视口
        <select
          aria-label="视口"
          value={viewport}
          onChange={(e) => {
            setViewport(e.target.value)
            setMasks([])
            setRegion(null)
            setDraft(null)
            origin.current = null
            setNotice('视口已改变，已清除旧坐标；请重新应用后点选。')
          }}
        >
          <option value="desktop">桌面 1000 × 720</option>
          <option value="mobile">手机 390 × 844</option>
        </select>
      </label>
      {enabled && (
        <>
          <KeyValueEditor
            label="合成浏览器数据"
            rows={storage}
            onChange={setStorage}
            limit={32}
            disabled={busy}
          />
          <details>
            <summary>高级：业务接口合成响应</summary>
            <p>仅用于受控后端。路径对应JSON字符串，未配置返回404，不请求真实业务数据。</p>
            <textarea
              aria-label="业务接口合成响应"
              value={api}
              onChange={(e) => setApi(e.target.value)}
              maxLength={16384}
            />
          </details>
          <label>
            点选用途
            <select
              aria-label="点选用途"
              value={mode}
              onChange={(e) => setMode(e.target.value as 'mask' | 'region')}
            >
              <option value="mask">忽略动态区域（遮罩）</option>
              <option value="region">只比较指定区域</option>
            </select>
          </label>
          {!canPick && <p>请先应用独立模式和所选视口，收到新截图后再点选。</p>}
          <div
            className="visual-region-picker"
            aria-label="视觉区域选择截图"
            style={{ touchAction: 'none', width: frame.width }}
            onPointerDown={(e) => {
              if (!canPick) return
              const box = e.currentTarget.getBoundingClientRect()
              origin.current = {
                x: ((e.clientX - box.left) * width) / box.width,
                y: ((e.clientY - box.top) * height) / box.height,
              }
              e.currentTarget.setPointerCapture(e.pointerId)
            }}
            onPointerMove={(e) => {
              if (!canPick || !origin.current) return
              const box = e.currentTarget.getBoundingClientRect()
              setDraft(
                selectionRect(
                  origin.current,
                  {
                    x: ((e.clientX - box.left) * width) / box.width,
                    y: ((e.clientY - box.top) * height) / box.height,
                  },
                  width,
                  height,
                ),
              )
            }}
            onPointerUp={(e) => {
              if (!canPick || !origin.current) return
              const box = e.currentTarget.getBoundingClientRect()
              const r = selectionRect(
                origin.current,
                {
                  x: ((e.clientX - box.left) * width) / box.width,
                  y: ((e.clientY - box.top) * height) / box.height,
                },
                width,
                height,
              )
              origin.current = null
              setDraft(null)
              if (r) add(r)
            }}
            onPointerCancel={() => {
              origin.current = null
              setDraft(null)
            }}
          >
            <img
              src={'data:image/png;base64,' + frame.image}
              alt="仅选取区域，不触发产品控件"
              draggable={false}
            />
            {canPick &&
              overlays.map((v, i) => (
                <span
                  key={i}
                  className={'visual-region ' + v.kind}
                  style={{
                    left: (v.r.x / width) * 100 + '%',
                    top: (v.r.y / height) * 100 + '%',
                    width: (v.r.width / width) * 100 + '%',
                    height: (v.r.height / height) * 100 + '%',
                  }}
                >
                  {v.label}
                </span>
              ))}
          </div>
          <button disabled={!canPick} onClick={() => add({ x: 0, y: 0, width: 100, height: 40 })}>
            添加区域后填写坐标
          </button>
          {[
            ...masks.map((rect, index) => ({ rect, index })),
            ...(region ? [{ rect: region, index: -1 }] : []),
          ].map(({ rect, index }) => (
            <fieldset key={index} className="rectangle-fields">
              <legend>{index < 0 ? '只比较区域' : `遮罩 ${index + 1}`}</legend>
              {(['x', 'y', 'width', 'height'] as const).map((k) => (
                <label key={k}>
                  {k}
                  <input
                    aria-label={`${index < 0 ? '比较区域' : '遮罩' + (index + 1)} ${k}`}
                    type="number"
                    min={k === 'x' || k === 'y' ? 0 : 1}
                    value={rect[k]}
                    onChange={(e) => {
                      const next = { ...rect, [k]: Number(e.target.value) }
                      if (index < 0) setRegion(next)
                      else setMasks(masks.map((r, i) => (i === index ? next : r)))
                    }}
                  />
                </label>
              ))}
              <button
                onClick={() =>
                  index < 0 ? setRegion(null) : setMasks(masks.filter((_, i) => i !== index))
                }
              >
                移除{index < 0 ? '比较区域' : '遮罩' + (index + 1)}
              </button>
            </fieldset>
          ))}
        </>
      )}
      <button
        disabled={busy}
        onClick={async () => {
          try {
            setError('')
            setNotice('')
            const result = await apply({
              enabled,
              viewport,
              storage: enabled ? pairsToRecord(storage) : {},
              api_fixtures: enabled ? JSON.parse(api) : {},
              masks: enabled ? masks : [],
              region: enabled ? region : null,
            })
            if (result === false)
              throw new Error('设置未应用，请查看预览错误；当前截图仍为上次结果。')
            setNotice('设置已应用，后续基线将绑定这些设置。')
          } catch (e) {
            setError(e instanceof Error ? e.message : '设置无效，请检查区域和合成数据。')
          }
        }}
      >
        应用设置并刷新截图
      </button>
      {notice && <p role="status">{notice}</p>}
      {error && (
        <p role="alert" className="inline-state error">
          {error}
        </p>
      )}
    </details>
  )
}
