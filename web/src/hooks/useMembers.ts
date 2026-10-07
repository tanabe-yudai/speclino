import { useEffect, useState } from 'react'
import { listMembers } from '@/api/members'
import { actorLabel } from '@/services/actors'
import type { WorkspaceMember } from '@/types/member'

/** slug のメンバーを 1 度だけ取得し、uid → 表示名の関数を返す。取れなくても画面は止めず「メンバー」にする。 */
export function useActorLabel(slug: string | null | undefined): (uid: string | null | undefined) => string {
  const [loaded, setLoaded] = useState<{ slug: string; members: WorkspaceMember[] } | null>(null)
  useEffect(() => {
    if (!slug) return
    let alive = true
    listMembers(slug)
      .then((members) => alive && setLoaded({ slug, members }))
      .catch(() => {})
    return () => {
      alive = false
    }
  }, [slug])
  const members = loaded && loaded.slug === slug ? loaded.members : null
  return (uid) => actorLabel(uid, members)
}
