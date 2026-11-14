export type Rect = { x: number; y: number; width: number; height: number }
export function selectionRect(
  start: { x: number; y: number },
  end: { x: number; y: number },
  width: number,
  height: number,
): Rect | null {
  const clamp = (v: number, limit: number) => Math.max(0, Math.min(limit, Math.round(v)))
  const x1 = clamp(start.x, width),
    x2 = clamp(end.x, width),
    y1 = clamp(start.y, height),
    y2 = clamp(end.y, height)
  if (x1 === x2 || y1 === y2) return null
  return {
    x: Math.min(x1, x2),
    y: Math.min(y1, y2),
    width: Math.abs(x2 - x1),
    height: Math.abs(y2 - y1),
  }
}
