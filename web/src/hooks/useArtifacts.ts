import { useCallback, useEffect, useState } from 'react'
import { ApiError } from '@/api/errors'
import { listArtifacts } from '@/api/knowledge'
import type { ArtifactSummary } from '@/types/knowledge'

interface Loaded {
  slug: string
  artifacts: ArtifactSummary[] | null
  error: string | null
}

/** slug のワークスペースの仕様・スキル・指示書の一覧。slug が変わった直後は前の一覧を返さず null。 */
export function useArtifacts(slug: string | undefined) {
  const [loaded, setLoaded] = useState<Loaded | null>(null)
  const [attempt, setAttempt] = useState(0)

  useEffect(() => {
    if (!slug) return
    let alive = true
    listArtifacts(slug)
      .then((artifacts) => alive && setLoaded({ slug, artifacts, error: null }))
      .catch((err: unknown) => {
        if (alive) setLoaded({ slug, artifacts: null, error: err instanceof ApiError ? err.message : '一覧を取得できませんでした' })
      })
    return () => {
      alive = false
    }
  }, [slug, attempt])

  const retry = useCallback(() => {
    setLoaded(null)
    setAttempt((n) => n + 1)
  }, [])

  const current = loaded && loaded.slug === slug ? loaded : null
  return { artifacts: current?.artifacts ?? null, error: current?.error ?? null, retry }
}
