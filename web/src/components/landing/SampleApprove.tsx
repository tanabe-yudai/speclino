import { Panel } from '@/components/app/Panel'
import { cn } from '@/lib/utils'
import { SAMPLE_APPROVE } from '@/services/landingContent'
import { Sample } from './Sample'

export function SampleApprove() {
  const a = SAMPLE_APPROVE
  return (
    <Sample label="エージェントが出した仕様の下書きを、画面で承認する見本">
      <Panel className="space-y-3 p-4">
        <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
          <span className="rounded-md bg-accent px-2 py-0.5 text-xs font-semibold text-primary">{a.kind}</span>
          <code className="font-mono text-sm font-semibold">{a.name}</code>
          <span className="text-xs text-ink-3">{a.revision}</span>
          <span className="text-xs text-ink-3">{a.author}</span>
        </div>
        <div className="rounded-lg bg-card p-2 font-mono text-[11px] leading-relaxed">
          {a.diff.map((d) => (
            <p key={d.text} className={cn('break-all', d.sign === '+' ? 'text-ok' : 'text-ink-3')}>
              {d.sign} {d.text}
            </p>
          ))}
        </div>
        <div className="flex flex-wrap items-center gap-3">
          <span className="rounded-lg bg-brand px-3 py-1.5 text-xs font-semibold text-white">{a.action}</span>
          <span className="text-xs text-ok">✓ {a.diagnostics}</span>
        </div>
        <p className="text-xs text-ink-2">{a.done}</p>
      </Panel>
    </Sample>
  )
}
