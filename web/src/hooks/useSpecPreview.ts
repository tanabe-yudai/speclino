import { useEffect, useState } from 'react'
import { previewSpec } from '@/api/knowledge'
import { ApiError } from '@/api/errors'
import type { SpecPreview, SpecPreviewRequest } from '@/types/knowledge'

const DEBOUNCE_MS = 500

interface State {
  key: string
  preview: SpecPreview | null
  error: string | null
}

/**
 * 本文のプレビュー（POST /specs/preview）。入力が止まってから 500ms 後に 1 回だけ呼び、
 * 古い要求は中断する。request が null なら呼ばない（JSON 欄が読めないときなど）。
 */
export function useSpecPreview(slug: string, request: SpecPreviewRequest | null) {
  const key = request ? JSON.stringify(request) : ''
  const [state, setState] = useState<State | null>(null)

  useEffect(() => {
    if (!key) return
    const controller = new AbortController()
    const timer = setTimeout(() => {
      previewSpec(slug, JSON.parse(key) as SpecPreviewRequest, controller.signal)
        .then((preview) => setState({ key, preview, error: null }))
        .catch((err: unknown) => {
          if (controller.signal.aborted) return
          setState({ key, preview: null, error: err instanceof ApiError ? err.message : 'プレビューを取得できませんでした' })
        })
    }, DEBOUNCE_MS)
    return () => {
      clearTimeout(timer)
      controller.abort()
    }
  }, [slug, key])

  const current = state && state.key === key ? state : null
  return {
    preview: current?.preview ?? null,
    error: current?.error ?? null,
    pending: !!key && current === null,
    /** 直前の結果（入力中の表示を空にしないため） */
    last: state?.preview ?? null,
  }
}
