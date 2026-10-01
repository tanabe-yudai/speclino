import { Panel } from '@/components/app/Panel'
import { SAMPLE_SPEC } from '@/services/landingContent'
import { Sample } from './Sample'

export function SampleSpec() {
  return (
    <Sample label="仕様の Markdown と、PR の承認コメントの見本">
      <Panel className="space-y-3 p-4 font-mono text-xs leading-relaxed">
        <p className="text-ink-3">{SAMPLE_SPEC.path}</p>
        <div>
          {SAMPLE_SPEC.lines.map((l, i) => (
            <p key={i} className="min-h-[1em] break-words">{l}</p>
          ))}
        </div>
        <p className="inline-block max-w-full rounded-lg bg-accent px-3 py-1.5 break-all text-primary">💬 {SAMPLE_SPEC.comment}</p>
      </Panel>
    </Sample>
  )
}
