import { useSearchParams } from 'react-router-dom'
import { ErrorState } from '@/components/ErrorState'
import { LoadingState } from '@/components/LoadingState'
import { useArtifacts } from '@/hooks/useArtifacts'
import { useWorkspaceProjects } from '@/hooks/useWorkspaceProjects'
import { KNOWLEDGE_KIND_LABELS } from '@/services/labels'
import { kindArtifacts, parseKnowledgeListFilter } from '@/services/knowledgeNav'
import { paths } from '@/services/paths'
import { KnowledgeRows } from './KnowledgeRows'
import { KnowledgeToolbar } from './KnowledgeToolbar'

/** スキル・指示書の一覧（ワークスペース全体、`?status=` で絞る）。取り下げ済みは出さない。 */
export function KindBrowser({ slug, kind }: { slug: string; kind: 'skill' | 'instructions' }) {
  const [params] = useSearchParams()
  const { status } = parseKnowledgeListFilter(params)
  const { artifacts, error, retry } = useArtifacts(slug)
  const { projects } = useWorkspaceProjects(slug)
  const rows = artifacts ? kindArtifacts(artifacts, kind, status) : null
  const projectNames = new Map((projects ?? []).map((p) => [p.id, p.repo_full_name.split('/')[1]]))
  const label = KNOWLEDGE_KIND_LABELS[kind]

  return (
    <section className="space-y-4" aria-label={label}>
      <KnowledgeToolbar
        kind={kind}
        status={status}
        statusPath={(s) => paths.knowledgeList(slug, kind, { status: s })}
        createPath={paths.knowledgeNew(slug, kind)}
      />
      {error && <ErrorState message={error} onRetry={retry} />}
      {!error && rows === null && <LoadingState message={`${label}の一覧を取得しています…`} />}
      {!error && rows !== null && (
        <KnowledgeRows
          slug={slug}
          artifacts={rows}
          from={null}
          projectNames={projectNames}
          label={`${label}の一覧`}
          empty={`条件に合う${label}はありません。`}
        />
      )}
    </section>
  )
}
