import { Link } from 'react-router-dom'
import { ErrorState } from '@/components/ErrorState'
import { LoadingState } from '@/components/LoadingState'
import { Button } from '@/components/ui/button'
import { KIND_LABELS } from '@/services/knowledge'
import { paths } from '@/services/paths'
import type { ArtifactKind } from '@/types/knowledge'
import type { ArtifactRowData } from '@/types/navigation'
import { ArtifactRows } from './ArtifactRows'

/**
 * ある種類の一覧と「作る」。rows が null の間は読み込み中。
 * from はプロジェクトのタブから開いたときの戻り先（owner/repo）。
 */
export function ArtifactSection({
  slug,
  kind,
  rows,
  error,
  onRetry,
  from,
  heading,
}: {
  slug: string
  kind: ArtifactKind
  rows: ArtifactRowData[] | null
  error: string | null
  onRetry: () => void
  from?: string | null
  heading?: string
}) {
  const label = KIND_LABELS[kind]
  return (
    <section className="space-y-3" aria-label={label}>
      <div className="flex flex-wrap items-center gap-3">
        {heading && <h2 className="text-sm font-semibold text-ink-2">{heading}</h2>}
        <Button asChild size="sm" className="ml-auto bg-brand text-white">
          <Link to={paths.knowledgeNew(slug, kind, from)}>{label}を作る</Link>
        </Button>
      </div>
      {error && <ErrorState message={error} onRetry={onRetry} />}
      {!error && rows === null && <LoadingState message={`${label}の一覧を取得しています…`} />}
      {rows !== null && <ArtifactRows slug={slug} kind={kind} rows={rows} from={from} />}
    </section>
  )
}
