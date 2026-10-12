import { ChevronDown, Plus } from 'lucide-react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { ErrorState } from '@/components/ErrorState'
import { LoadingState } from '@/components/LoadingState'
import { TabNav } from '@/components/shell/TabNav'
import { Button } from '@/components/ui/button'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { useArtifacts } from '@/hooks/useArtifacts'
import { usePolicySummary } from '@/hooks/usePolicySummary'
import { useWorkspaceProjects } from '@/hooks/useWorkspaceProjects'
import { KNOWLEDGE_KIND_LABELS, KNOWLEDGE_STATE_LABELS } from '@/services/labels'
import { KNOWLEDGE_KINDS, KNOWLEDGE_STATUS_FILTERS, knowledgeRows, parseKnowledgeFilter, showPolicyRow } from '@/services/knowledgeNav'
import { paths } from '@/services/paths'
import type { KnowledgeFilter, KnowledgeKind } from '@/types/navigation'
import type { ProjectSummary } from '@/types/project'
import { KnowledgeRows } from './KnowledgeRows'

const ALL = 'all'

/**
 * 知識の一覧（仕様・スキル・指示書・規約）と絞り込み（種類・プロジェクト・状態、URL のクエリ）。
 * project を渡すとそのプロジェクトで絞った一覧（プロジェクトの知識のタブ）になり、プロジェクトは選ばせない。
 */
export function KnowledgeBrowser({ slug, project }: { slug: string; project?: ProjectSummary }) {
  const [params] = useSearchParams()
  const navigate = useNavigate()
  const parsed = parseKnowledgeFilter(params)
  const filter: KnowledgeFilter = project ? { ...parsed, project: project.repo_full_name } : parsed
  const { artifacts, error, retry } = useArtifacts(slug)
  const { projects } = useWorkspaceProjects(slug)
  const policy = usePolicySummary(slug)

  const listPath = (f: KnowledgeFilter) =>
    project ? paths.project(slug, project.repo_full_name, 'knowledge', f) : paths.knowledge(slug, f)
  const from = filter.project
  const rows = artifacts ? knowledgeRows(artifacts, filter, projects) : null
  const policyRow = policy.row && showPolicyRow(policy.row, filter) ? policy.row : null
  const projectNames = new Map((projects ?? []).map((p) => [p.id, p.repo_full_name.split('/')[1]]))

  return (
    <section className="space-y-4" aria-label="知識">
      <div className="flex flex-wrap items-center gap-3">
        <TabNav
          label="種類で絞る"
          current={filter.kind ?? ALL}
          items={[
            { id: ALL, label: 'すべて', to: listPath({ ...filter, kind: null }) },
            ...KNOWLEDGE_KINDS.map((k) => ({ id: k, label: KNOWLEDGE_KIND_LABELS[k], to: listPath({ ...filter, kind: k }) })),
          ]}
        />
        <CreateWithAi slug={slug} kind={filter.kind} from={from} />
      </div>
      <div className="flex flex-wrap items-center gap-3">
        <TabNav
          label="状態で絞る"
          current={filter.status ?? ALL}
          items={[
            { id: ALL, label: 'すべての状態', to: listPath({ ...filter, status: null }) },
            ...KNOWLEDGE_STATUS_FILTERS.map((s) => ({ id: s, label: KNOWLEDGE_STATE_LABELS[s], to: listPath({ ...filter, status: s }) })),
          ]}
        />
        {!project && (
          <label className="flex items-center gap-2 text-sm text-ink-2">
            プロジェクト
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
      </div>
      {error && <ErrorState message={error} onRetry={retry} />}
      {!error && (rows === null || (filter.project && projects === null)) && <LoadingState message="知識の一覧を取得しています…" />}
      {!error && rows !== null && !(filter.project && projects === null) && (
        <KnowledgeRows slug={slug} rows={rows} policy={policyRow} from={from} projectNames={projectNames} />
      )}
    </section>
  )
}

/** 「+ AI と作る」。種類で絞っていればその種類のエディタ、無ければ種類を選ぶ。規約は規約の画面で作る。 */
function CreateWithAi({ slug, kind, from }: { slug: string; kind: KnowledgeKind | null; from: string | null }) {
  const navigate = useNavigate()
  const target = (k: KnowledgeKind) => (k === 'policy' ? paths.policies(slug) : paths.knowledgeNew(slug, k, from))
  if (kind) {
    return (
      <Button asChild size="sm" className="ml-auto bg-brand text-white">
        <Link to={target(kind)}>
          <Plus className="size-4" /> AI と{KNOWLEDGE_KIND_LABELS[kind]}を作る
        </Link>
      </Button>
    )
  }
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button size="sm" className="ml-auto bg-brand text-white">
          <Plus className="size-4" /> AI と作る <ChevronDown className="size-3.5" />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end">
        {KNOWLEDGE_KINDS.map((k) => (
          <DropdownMenuItem key={k} onSelect={() => navigate(target(k))}>
            {KNOWLEDGE_KIND_LABELS[k]}
          </DropdownMenuItem>
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
  )
}
