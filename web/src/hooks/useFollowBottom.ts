import { useCallback, useLayoutEffect, useRef } from 'react'
import { nearBottom } from '@/services/chatDraft'

/**
 * スクロールする一覧の末尾を追う（S79）。`signal` が変わるたびに、末尾の近くにいれば末尾へ動かす。
 * 上へ読み戻しているときは追わない。`resetKey` が変わったとき（別の会話）と `follow()` の後は必ず追う。
 * 動かすのは一覧の中だけで、ページ全体はスクロールしない。
 */
export function useFollowBottom<T extends HTMLElement>(signal: unknown, resetKey: unknown) {
  const ref = useRef<T>(null)
  const following = useRef(true)
  const lastKey = useRef(resetKey)

  const onScroll = useCallback(() => {
    const el = ref.current
    if (el) following.current = nearBottom(el)
  }, [])
  const follow = useCallback(() => {
    following.current = true
  }, [])

  useLayoutEffect(() => {
    if (lastKey.current !== resetKey) {
      lastKey.current = resetKey
      following.current = true
    }
    const el = ref.current
    if (el && following.current) el.scrollTop = el.scrollHeight
  }, [signal, resetKey])

  return { ref, onScroll, follow }
}
