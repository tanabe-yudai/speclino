import { useReveal } from '@/hooks/useReveal'
import { cn } from '@/lib/utils'
import { SAMPLE_SESSION } from '@/services/landingContent'
import type { SessionLine } from '@/types/landing'
import { Sample } from './Sample'

const LINE_STYLE: Record<SessionLine['kind'], string> = {
  user: 'text-[#e8ecf8]',
  tool: 'text-[#8fb4ff]',
  result: 'pl-4 text-[#9aa6c4]',
  wait: 'pl-4 text-[#f5c26b]',
}

const PREFIX: Record<SessionLine['kind'], string> = { user: '›', tool: '⏺', result: '⎿', wait: '⎿' }

/** ヒーローの見本: 利用者のエージェントが MCP で仕様を書き、検査まで回すセッション。 */
export function SampleSession() {
  const lines = SAMPLE_SESSION.lines
  const shown = useReveal(lines.length, 700)
  const last = lines.at(-1)
  return (
    <Sample label="エディタのコーディングエージェントが、MCP で仕様を読み、下書きに出し、検査にかける見本">
      <div className="overflow-hidden rounded-2xl bg-[#131c33] shadow-lift">
        <div className="flex items-center gap-1.5 border-b border-white/10 px-4 py-2.5">
          <span className="size-2.5 rounded-full bg-white/15" />
          <span className="size-2.5 rounded-full bg-white/15" />
          <span className="size-2.5 rounded-full bg-white/15" />
          <span className="ml-2 font-mono text-[11px] text-[#9aa6c4]">{SAMPLE_SESSION.label}</span>
        </div>
        <div className="min-h-[14.5rem] space-y-1.5 p-4 font-mono text-xs leading-relaxed">
          {lines.slice(0, shown).map((l, i) => (
            <p
              key={i}
              className={cn(
                'break-words',
                LINE_STYLE[l.kind],
                l === last && 'text-[#ff9b8f]',
                i > 0 && l.kind === 'user' && 'mt-3',
              )}
            >
              <span className="mr-2 opacity-70">{PREFIX[l.kind]}</span>
              {l.text}
            </p>
          ))}
          {shown < lines.length && <span className="inline-block h-3.5 w-2 bg-[#8fb4ff] motion-safe:animate-pulse" />}
        </div>
      </div>
    </Sample>
  )
}
