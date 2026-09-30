/** キーごとに取得の Promise を覚える（同じ一覧を画面の複数箇所で二重に取らない）。失敗は覚えない。 */
export function createRequestCache<T>(fetcher: (key: string) => Promise<T>) {
  const entries = new Map<string, Promise<T>>()
  return {
    get(key: string): Promise<T> {
      const hit = entries.get(key)
      if (hit) return hit
      const p = fetcher(key)
      entries.set(key, p)
      p.catch(() => {
        if (entries.get(key) === p) entries.delete(key)
      })
      return p
    },
    invalidate(key?: string): void {
      if (key === undefined) entries.clear()
      else entries.delete(key)
    },
  }
}
