import { Panel } from '@/components/app/Panel'
import { ToneBadge } from '@/components/app/ToneBadge'
import { HealthRing } from '@/components/project/HealthRing'
import { NEXT_ACTION_KIND } from '@/services/labels'
import { SAMPLE_PROJECT } from '@/services/landingContent'
import { Sample } from './Sample'


export function SampleProject() {
  const c = SAMPLE_PROJECT.counts
  return (
    <Sample label="プロジェクト画面の見本: 健康状態と次にやること">
      <Panel className="flex flex-wrap items-start gap-4 p-4">
        <HealthRing size="lg" counts={c} />
        <div className="min-w-0 flex-1 space-y-2">
          <p className="font-semibold">{SAMPLE_PROJECT.name}</p>
          <p className="flex flex-wrap gap-x-3 text-xs">
            <span className="text-bad">違反 {c.violated}</span>
            <span className="text-rev">要確認 {c.review}</span>
            <span className="text-unk">未検証 {c.unknown}</span>
            <span className="text-exempt">免除中 {c.exempted}</span>
            <span className="text-ok">適合 {c.ok}</span>
          </p>
          <p className="text-xs font-semibold text-primary">✦ 次にやること</p>
          <ul className="space-y-1">
            {SAMPLE_PROJECT.next.map((n) => (
              <li key={n.id} className="flex min-w-0 flex-wrap items-center gap-2 text-xs">
                <ToneBadge tone={NEXT_ACTION_KIND[n.kind].tone}>{NEXT_ACTION_KIND[n.kind].label}</ToneBadge>
                <code className="font-mono">{n.id}</code>
                <span className="text-ink-3">{n.hint}</span>
              </li>
            ))}
          </ul>
        </div>
      </Panel>
    </Sample>
  )
}
