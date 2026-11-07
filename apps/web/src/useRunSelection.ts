import { useCallback, useEffect, useRef, useState } from 'react'
import { API_BASE, apiJson } from './api'
import { RunSelection } from './runSelection'
import { terminalStates, type RunRecord } from './runTypes'

export function useRunSelection(onTerminal: () => void) {
  const [run, display] = useState<RunRecord | null>(null)
  const [runs, records] = useState<RunRecord[]>([])
  const [selectionRevision, selected] = useState(0)
  const gate = useRef(new RunSelection())
  const terminal = useRef(onTerminal)
  terminal.current = onTerminal
  const remember = useCallback((next: RunRecord) => {
    records((items) => [next, ...items.filter((item) => item.run_id !== next.run_id)])
  }, [])
  const setRun = useCallback(
    (next: RunRecord | null) => {
      if (next) {
        gate.current.begin(next.run_id, true)
        remember(next)
      } else gate.current.clear()
      selected(gate.current.revision)
      display(next)
    },
    [remember],
  )
  const read = useCallback(
    async (id: string, select: boolean) => {
      const ticket = gate.current.begin(id, select)
      if (select) selected(gate.current.revision)
      try {
        const next = await apiJson<RunRecord>('/runs/' + id)
        if (gate.current.latest(ticket)) remember(next)
        if (!gate.current.visible(ticket)) return null
        display(next)
        return next
      } catch (error) {
        if (!gate.current.visible(ticket)) return null
        throw error
      }
    },
    [remember],
  )
  const refreshRun = useCallback((id: string) => read(id, true), [read])
  const updateRun = useCallback((id: string) => read(id, false), [read])
  useEffect(() => {
    let active = true
    const initialRevision = gate.current.revision
    const controller = new AbortController()
    void apiJson<RunRecord[]>('/runs', { signal: controller.signal })
      .then((items) => {
        if (!active || gate.current.revision !== initialRevision) return
        records(items)
        if (items.length) setRun(items[0])
      })
      .catch(() => {})
    return () => {
      active = false
      controller.abort()
      gate.current.clear()
    }
  }, [setRun])
  useEffect(() => {
    if (!run || gate.current.id !== run.run_id || terminalStates.has(run.status)) return
    let active = true
    const id = run.run_id
    const selection = gate.current.revision
    const source = new EventSource(API_BASE + '/runs/' + id + '/events')
    source.onmessage = (event) => {
      if (!active || gate.current.id !== id || gate.current.revision !== selection) return
      try {
        const next = JSON.parse(event.data) as RunRecord
        if (next.run_id !== id) return
        gate.current.begin(id) // Invalidate older HTTP replies for this project.
        remember(next)
        display(next)
        if (terminalStates.has(next.status)) {
          source.close()
          terminal.current()
        }
      } catch {
        /* EventSource reconnects; malformed frames never replace displayed data. */
      }
    }
    return () => {
      active = false
      source.close()
    }
  }, [selectionRevision, run?.run_id, run ? terminalStates.has(run.status) : true, remember])
  return { run, runs, setRun, refreshRun, updateRun }
}
