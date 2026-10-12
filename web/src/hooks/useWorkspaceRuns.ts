import { useCallback, useEffect, useState } from 'react'
import { ApiError } from '@/api/errors'
import { listRuns } from '@/api/reviewRuns'
import type { ReviewRunSummary } from '@/types/run'

/** 一覧に読む検査の数（API の上限）。 */
export const RUN_LIST_LIMIT = 200

interface Loaded {
  slug: string
  runs: ReviewRunSummary[] | null
  error: string | null
}

/** slug のワークスペースの検査（新しい順、API 側でワークスペースに絞る）。slug が変わった直後は null。 */
export function useWorkspaceRuns(slug: string | undefined, limit = RUN_LIST_LIMIT) {
  const [loaded, setLoaded] = useState<Loaded | null>(null)
  const [attempt, setAttempt] = useState(0)

  useEffect(() => {
    if (!slug) return
    let alive = true
    listRuns(limit, slug)
      .then((runs) => alive && setLoaded({ slug, runs, error: null }))
      .catch((err: unknown) => {
        if (alive) setLoaded({ slug, runs: null, error: err instanceof ApiError ? err.message : '検査の一覧を取得できませんでした' })
      })
    return () => {
      alive = false
    }
  }, [slug, limit, attempt])

  const retry = useCallback(() => {
    setLoaded(null)
    setAttempt((n) => n + 1)
  }, [])

  const current = loaded && loaded.slug === slug ? loaded : null
  return { runs: current?.runs ?? null, error: current?.error ?? null, retry }
}
