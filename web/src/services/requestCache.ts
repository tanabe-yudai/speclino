/**
 * キーごとに取得の Promise を覚える（同じ一覧を画面の複数箇所で二重に取らない）。
 * 失敗は覚えない。ttlMs を過ぎたら取り直す（新しい検査の結果を古いまま出し続けない）。
 */
export function createRequestCache<T>(
  fetcher: (key: string) => Promise<T>,
  options: { ttlMs?: number; now?: () => number } = {},
) {
  const { ttlMs = Infinity, now = Date.now } = options
  const entries = new Map<string, { promise: Promise<T>; at: number }>()
  return {
    get(key: string): Promise<T> {
      const hit = entries.get(key)
      if (hit && now() - hit.at < ttlMs) return hit.promise
      const promise = fetcher(key)
      const entry = { promise, at: now() }
      entries.set(key, entry)
      promise.catch(() => {
        if (entries.get(key) === entry) entries.delete(key)
      })
      return promise
    },
    invalidate(key?: string): void {
      if (key === undefined) entries.clear()
      else entries.delete(key)
    },
  }
}
