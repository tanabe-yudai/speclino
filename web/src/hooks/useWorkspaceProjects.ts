import { useCallback, useEffect, useState } from 'react'
import { ApiError } from '@/api/errors'
import { invalidateProjects, listProjects } from '@/api/projects'
import type { ProjectSummary } from '@/types/project'

interface Loaded {
  slug: string
  projects: ProjectSummary[] | null
  error: string | null
}

/** slug のワークスペースのプロジェクト一覧。slug が変わった直後は前の一覧を返さず null。 */
export function useWorkspaceProjects(slug: string | undefined) {
  const [loaded, setLoaded] = useState<Loaded | null>(null)
  const [attempt, setAttempt] = useState(0)

  useEffect(() => {
    if (!slug) return
    let alive = true
    listProjects(slug)
      .then((projects) => alive && setLoaded({ slug, projects, error: null }))
      .catch((err: unknown) => {
        if (alive) setLoaded({ slug, projects: null, error: err instanceof ApiError ? err.message : 'プロジェクトの一覧を取得できませんでした' })
      })
    return () => {
      alive = false
    }
  }, [slug, attempt])

  const retry = useCallback(() => {
    if (slug) invalidateProjects(slug)
    setLoaded(null)
    setAttempt((n) => n + 1)
  }, [slug])

  const current = loaded && loaded.slug === slug ? loaded : null
  return { projects: current?.projects ?? null, error: current?.error ?? null, retry }
}
