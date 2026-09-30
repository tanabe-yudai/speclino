import { Panel } from '@/components/app/Panel'
import { SAMPLE_REPO } from '@/services/landingContent'
import { Sample } from './Sample'

export function SampleRepo() {
  return (
    <Sample label="リポジトリの登録画面の見本">
      <Panel className="space-y-2 p-4">
        <p className="text-xs text-ink-3">リポジトリ（{SAMPLE_REPO.workspace}）</p>
        {SAMPLE_REPO.repositories.map((r) => (
          <div key={r.name} className="flex min-w-0 flex-wrap items-center gap-2 rounded-lg bg-secondary px-3 py-2 text-sm">
            <span className="min-w-0 truncate font-mono text-xs">{r.name}</span>
            <span className="text-xs text-ink-3">既定ブランチ {r.branch}</span>
          </div>
        ))}
        <div className="flex flex-wrap gap-2">
          <span className="min-w-0 flex-1 rounded-lg border bg-card px-3 py-2 text-sm text-ink-3">{SAMPLE_REPO.inputPlaceholder}</span>
          <span className="rounded-lg bg-primary px-3 py-2 text-sm font-medium text-primary-foreground">{SAMPLE_REPO.action}</span>
        </div>
      </Panel>
    </Sample>
  )
}
