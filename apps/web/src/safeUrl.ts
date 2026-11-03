/** Persisted reference metadata is untrusted, even when React escapes the label. */
export function publicHttpsUrl(value: string): string | undefined {
  try {
    const url = new URL(value)
    if (url.protocol !== 'https:' || url.username || url.password || (url.port && url.port !== '443')) return undefined
    return url.href
  } catch { return undefined }
}
