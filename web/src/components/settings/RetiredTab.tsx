import { Panel, PanelTitle } from '@/components/app/Panel'
import { ErrorState } from '@/components/ErrorState'
import { KnowledgeRows } from '@/components/knowledge/KnowledgeRows'
import { LoadingState } from '@/components/LoadingState'
import { useArtifacts } from '@/hooks/useArtifacts'
import { useWorkspaceProjects } from '@/hooks/useWorkspaceProjects'
import { RETIRED_RESTORE_HINT, SETTINGS_TAB_LABELS } from '@/services/labels'
import { retiredArtifacts } from '@/services/knowledgeNav'

/** 取り下げ済みの知識（種類の画面・ホーム・件数には出さない）と、戻し方の案内。名前からエディタを開く。 */
export function RetiredTab({ slug }: { slug: string }) {
  const { artifacts, error, retry } = useArtifacts(slug)
  const { projects } = useWorkspaceProjects(slug)
  const projectNames = new Map((projects ?? []).map((p) => [p.id, p.repo_full_name.split('/')[1]]))
  const rows = artifacts ? retiredArtifacts(artifacts) : null
  return (
    <div className="space-y-4">
      <Panel className="space-y-1">
        <PanelTitle>戻し方</PanelTitle>
        <p className="text-sm text-ink-2">{RETIRED_RESTORE_HINT}</p>
      </Panel>
      {error && <ErrorState message={error} onRetry={retry} />}
      {!error && rows === null && <LoadingState message="取り下げ済みの知識を取得しています…" />}
      {!error && rows !== null && (
        <KnowledgeRows
          slug={slug}
          artifacts={rows}
          from={null}
          projectNames={projectNames}
          showKind
          label={SETTINGS_TAB_LABELS.retired}
          empty="取り下げ済みの知識はありません。"
        />
      )}
    </div>
  )
}
