import { useEffect, useRef, useState } from 'react'
import { ApiError } from '@/api/errors'
import { suggestSpec } from '@/api/suggestions'
import { afterApply, applySuggestion, toPending } from '@/services/suggestions'
import type { SpecPreviewRequest } from '@/types/knowledge'
import type { PendingSuggestion, SuggestionPanelState } from '@/types/suggestion'

/**
 * AI の書き換え提案の状態（S67）。タブを切り替えても失わないよう、エディタの側（常に表示される親）が持つ。
 * 適用は本文を返すだけで、編集欄への反映（onApply）は呼び出し側が行う。保存はしない。
 */
export function useSuggestions(slug: string, markdown: string, onApply: (markdown: string) => void) {
  const [state, setState] = useState<SuggestionPanelState>({ kind: 'idle' })
  const controller = useRef<AbortController | null>(null)
  useEffect(() => () => controller.current?.abort(), [])

  function request(body: SpecPreviewRequest) {
    controller.current?.abort()
    const c = new AbortController()
    controller.current = c
    const base = body.markdown
    setState({ kind: 'loading' })
    suggestSpec(slug, body, c.signal)
      .then((res) => setState({ kind: 'ready', base, items: toPending(base, res.suggestions), errorsBefore: res.errors_before, dropped: 0 }))
      .catch((err: unknown) => {
        if (c.signal.aborted) return
        setState({ kind: 'error', message: err instanceof ApiError ? err.message : '提案を取得できませんでした' })
      })
  }

  const ready = state.kind === 'ready' ? state : null
  const stale = ready !== null && ready.base !== markdown

  function apply(s: PendingSuggestion) {
    if (!ready || stale) return
    const next = applySuggestion(ready.base, s)
    const { remaining, dropped } = afterApply(ready.items, s)
    setState({ ...ready, base: next, items: remaining, dropped: ready.dropped + dropped })
    onApply(next)
  }

  function reject(s: PendingSuggestion) {
    if (!ready) return
    setState({ ...ready, items: ready.items.filter((x) => x.id !== s.id) })
  }

  return { state, stale, request, apply, reject }
}
