import type { ReactNode } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { ErrorState } from '@/components/ErrorState'
import { LoadingState } from '@/components/LoadingState'
import { useArtifacts } from '@/hooks/useArtifacts'
import { useWorkspaceProjects } from '@/hooks/useWorkspaceProjects'
import { KNOWLEDGE_KIND_LABELS, SPEC_GROUP_HINTS, SPEC_GROUP_LABELS } from '@/services/labels'
import { kindArtifacts, parseKnowledgeListFilter, specGroups } from '@/services/knowledgeNav'
import { paths } from '@/services/paths'
import type { ArtifactSummary } from '@/types/knowledge'
import type { KnowledgeListFilter } from '@/types/navigation'
import type { ProjectSummary } from '@/types/project'
import { KnowledgeRows } from './KnowledgeRows'
import { KnowledgeToolbar } from './KnowledgeToolbar'

/**
 * 仕様の一覧。「ワークスペース全体の仕様」と「リポジトリ別の仕様」の 2 つの節（振り分けは specGroups）。
 * project を渡すとプロジェクトの仕様のタブ（このリポジトリの仕様＋ワークスペース全体の仕様）になる。
 * 取り下げ済みは出さない。
 */
export function SpecBrowser({ slug, project }: { slug: string; project?: ProjectSummary }) {
  const [params] = useSearchParams()
  const navigate = useNavigate()
  const filter = parseKnowledgeListFilter(params)
  const repo = project?.repo_full_name ?? filter.project
  const { artifacts, error, retry } = useArtifacts(slug)
  const { projects, error: projectsError, retry: retryProjects } = useWorkspaceProjects(slug)

  const listPath = (f: KnowledgeListFilter) =>
    project ? paths.project(slug, project.repo_full_name, 'specs', f) : paths.knowledgeList(slug, 'spec', f)
  const groups = artifacts && projects ? specGroups(kindArtifacts(artifacts, 'spec', filter.status), projects, repo) : null
  const projectNames = new Map((projects ?? []).map((p) => [p.id, p.repo_full_name.split('/')[1]]))
  const from = project?.repo_full_name ?? null
  const rows = (items: ArtifactSummary[], label: string, empty: string) => (
    <KnowledgeRows slug={slug} artifacts={items} from={from} projectNames={projectNames} label={label} empty={empty} />
  )

  return (
    <div className="space-y-6">
      <KnowledgeToolbar
        kind="spec"
        status={filter.status}
        statusPath={(s) => listPath({ ...filter, status: s })}
        createPath={paths.knowledgeNew(slug, 'spec', repo)}
      >
        {!project && (
          <label className="flex items-center gap-2 text-sm text-ink-2">
            リポジトリ
            <select
              className="h-8 max-w-[14rem] rounded-md border bg-background px-2 text-sm"
              value={filter.project ?? ''}
              onChange={(e) => navigate(listPath({ ...filter, project: e.target.value || null }), { replace: true })}
            >
              <option value="">すべて</option>
              {(projects ?? []).map((p) => (
                <option key={p.id} value={p.repo_full_name}>
                  {p.repo_full_name}
                </option>
              ))}
            </select>
          </label>
        )}
      </KnowledgeToolbar>
      {error && <ErrorState message={error} onRetry={retry} />}
      {!error && projectsError && <ErrorState message={projectsError} onRetry={retryProjects} />}
      {!error && !projectsError && groups === null && <LoadingState message="仕様の一覧を取得しています…" />}
      {groups !== null && project && (
        <>
          <SpecSection id="this-repo" title={SPEC_GROUP_LABELS.thisRepository}>
            {rows(groups.repositories[0]?.specs ?? [], SPEC_GROUP_LABELS.thisRepository, 'このリポジトリだけに当たる仕様はありません。')}
          </SpecSection>
          <SpecSection id="workspace" title={SPEC_GROUP_LABELS.workspace} hint={SPEC_GROUP_HINTS.workspace}>
            {rows(groups.workspace, SPEC_GROUP_LABELS.workspace, 'ワークスペース全体の仕様はありません。')}
          </SpecSection>
          <p className="text-sm text-ink-2">
            スキル・指示書・規約はワークスペース全体に効きます:{' '}
            {(['skill', 'instructions', 'policy'] as const).map((k, i) => (
              <span key={k}>
                {i > 0 && '・'}
                <Link className="text-primary underline-offset-4 hover:underline" to={paths.knowledgeList(slug, k)}>
                  {KNOWLEDGE_KIND_LABELS[k]}
                </Link>
              </span>
            ))}
          </p>
        </>
      )}
      {groups !== null && !project && (
        <>
          <SpecSection id="workspace" title={SPEC_GROUP_LABELS.workspace} hint={SPEC_GROUP_HINTS.workspace}>
            {rows(groups.workspace, SPEC_GROUP_LABELS.workspace, 'ワークスペース全体の仕様はありません。')}
          </SpecSection>
          <SpecSection id="repositories" title={SPEC_GROUP_LABELS.repositories} hint={SPEC_GROUP_HINTS.repositories}>
            {groups.repositories.length === 0 && rows([], SPEC_GROUP_LABELS.repositories, 'リポジトリ別の仕様はありません。')}
            {groups.repositories.map(({ project: p, specs }) => (
              <div key={p?.id ?? 'unknown'} className="space-y-2">
                <h3 className="text-sm font-semibold">
                  {p ? (
                    <Link className="font-mono text-primary underline-offset-4 hover:underline" to={paths.project(slug, p.repo_full_name, 'specs')}>
                      {p.repo_full_name}
                    </Link>
                  ) : (
                    SPEC_GROUP_LABELS.unknownRepository
                  )}
                </h3>
                {rows(specs, `${p?.repo_full_name ?? SPEC_GROUP_LABELS.unknownRepository}の仕様`, '')}
              </div>
            ))}
          </SpecSection>
        </>
      )}
    </div>
  )
}

function SpecSection({ id, title, hint, children }: { id: string; title: string; hint?: string; children: ReactNode }) {
  return (
    <section className="space-y-3" aria-labelledby={`spec-${id}-heading`}>
      <div>
        <h2 id={`spec-${id}-heading`} className="text-base font-semibold">
          {title}
        </h2>
        {hint && <p className="mt-0.5 text-xs text-ink-3">{hint}</p>}
      </div>
      {children}
    </section>
  )
}
