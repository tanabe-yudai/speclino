import { describe, expect, it, vi } from 'vitest'
import { createRequestCache } from './requestCache'

describe('createRequestCache', () => {
  it('同じキーは 1 回だけ取りに行く', async () => {
    const fetcher = vi.fn(async (k: string) => `v:${k}`)
    const cache = createRequestCache(fetcher)
    const [a, b] = await Promise.all([cache.get('x'), cache.get('x')])
    expect([a, b]).toEqual(['v:x', 'v:x'])
    expect(fetcher).toHaveBeenCalledTimes(1)
  })

  it('失敗したら覚えず、次は取り直す', async () => {
    let n = 0
    const cache = createRequestCache(async () => {
      n += 1
      if (n === 1) throw new Error('boom')
      return 'ok'
    })
    await expect(cache.get('x')).rejects.toThrow('boom')
    await expect(cache.get('x')).resolves.toBe('ok')
  })

  it('invalidate で取り直す（キー指定と全部）', async () => {
    const fetcher = vi.fn(async (k: string) => k)
    const cache = createRequestCache(fetcher)
    await cache.get('a')
    await cache.get('b')
    cache.invalidate('a')
    await cache.get('a')
    await cache.get('b')
    expect(fetcher).toHaveBeenCalledTimes(3)
    cache.invalidate()
    await cache.get('b')
    expect(fetcher).toHaveBeenCalledTimes(4)
  })

  it('有効期限を過ぎたら取り直す（新しい検査の結果を古いまま出し続けない）', async () => {
    let now = 0
    const fetcher = vi.fn(async (k: string) => k)
    const cache = createRequestCache(fetcher, { ttlMs: 1000, now: () => now })
    await cache.get('a')
    now = 999
    await cache.get('a')
    expect(fetcher).toHaveBeenCalledTimes(1)
    now = 1000
    await cache.get('a')
    expect(fetcher).toHaveBeenCalledTimes(2)
  })
})
