import { PageHeader } from '@/components/app/PageHeader'
import { KindBrowser } from '@/components/knowledge/KindBrowser'
import { NotFoundPanel } from '@/components/knowledge/NotFoundPanel'
import { SpecBrowser } from '@/components/knowledge/SpecBrowser'
import { useCurrentWorkspace } from '@/hooks/useCurrentWorkspace'
import { useDocumentTitle } from '@/hooks/useDocumentTitle'
import { KNOWLEDGE_KIND_LABELS, KNOWLEDGE_PAGE_DESCRIPTIONS } from '@/services/labels'

const EYEBROW = { spec: 'Specs', skill: 'Skills', instructions: 'Instructions' } as const

/** 種類ごとの画面（`/w/:slug/specs`・`/skills`・`/instructions`）。規約は Policies。 */
export function KnowledgeList({ kind }: { kind: 'spec' | 'skill' | 'instructions' }) {
  const workspace = useCurrentWorkspace()
  const title = KNOWLEDGE_KIND_LABELS[kind]
  useDocumentTitle(title)
  if (!workspace) return <NotFoundPanel title="このワークスペースは見つかりません" />
  return (
    <div className="space-y-6">
      <PageHeader eyebrow={EYEBROW[kind]} title={title}>
        {KNOWLEDGE_PAGE_DESCRIPTIONS[kind]}
      </PageHeader>
      {kind === 'spec' ? (
        <SpecBrowser key={workspace.slug} slug={workspace.slug} />
      ) : (
        <KindBrowser key={`${workspace.slug}/${kind}`} slug={workspace.slug} kind={kind} />
      )}
    </div>
  )
}
