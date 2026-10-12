import { useEffect, useState } from 'react'
import { getPolicies } from '@/api/policies'
import { policyRowData } from '@/services/knowledgeNav'
import type { PolicyRowData } from '@/types/navigation'

interface Loaded {
  slug: string
  row: PolicyRowData | null
  failed: boolean
}

/** 規約（チーム規約）の行。一覧の補助なので、取れなければ行を出さない（failed）。 */
export function usePolicySummary(slug: string | undefined) {
  const [loaded, setLoaded] = useState<Loaded | null>(null)
  useEffect(() => {
    if (!slug) return
    let alive = true
    getPolicies(slug)
      .then((view) => alive && setLoaded({ slug, row: policyRowData(view), failed: false }))
      .catch(() => alive && setLoaded({ slug, row: null, failed: true }))
    return () => {
      alive = false
    }
  }, [slug])
  const current = loaded && loaded.slug === slug ? loaded : null
  return { row: current?.row ?? null, failed: current?.failed ?? false, loading: current === null }
}
