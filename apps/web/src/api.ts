/** Single local transport. Never retries mutations or contacts model providers. */
export const API_BASE = 'http://127.0.0.1:8000/api/v1'
export async function apiFetch(input: RequestInfo | URL, init?: RequestInit): Promise<Response> {
  try {
    return await fetch(input, init)
  } catch (error) {
    if (error instanceof Error && error.name === 'AbortError') throw error
    throw new Error(
      '本地服务连接中断，请确认工作台已启动。操作结果未知时，请先刷新状态，不要重复提交。',
    )
  }
}
export async function readJson<T>(
  response: Response,
  fallback = '本地操作未完成，请刷新状态后重试。',
): Promise<T> {
  const value = await response.json().catch(() => null)
  if (!response.ok) {
    const detail = value?.detail
    throw new Error(
      typeof detail === 'string'
        ? detail
        : typeof detail?.message === 'string'
          ? detail.message
          : fallback,
    )
  }
  if (value === null) throw new Error('本地服务返回了无法读取的结果，请刷新状态。')
  return value as T
}
export async function apiJson<T>(path: string, init?: RequestInit): Promise<T> {
  return readJson<T>(await apiFetch(API_BASE + path, init))
}
