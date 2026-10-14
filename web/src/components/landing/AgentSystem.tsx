import { Bot, Network, ScanSearch, ShieldCheck, Sparkles, UserCheck, type LucideIcon } from 'lucide-react'
import { AGENT_ROLES, AGENTS_SECTION } from '@/services/landingContent'
import type { AgentRole } from '@/types/landing'

const ROLE_ICON: Record<AgentRole['key'], LucideIcon> = {
  coding: Bot,
  writer: Sparkles,
  investigator: ScanSearch,
  a2a: Network,
}

function RoleCard({ role, side }: { role: AgentRole; side: 'left' | 'right' }) {
  const Icon = ROLE_ICON[role.key]
  const highlight = role.key === 'coding'
  return (
    <div
      className={
        'relative rounded-2xl bg-card/80 p-5 shadow-soft ' +
        (highlight ? 'ring-2 ring-brand/40 ' : '') +
        // 中央の SpecLino へつなぐ線（横に並ぶ幅のときだけ）
        (side === 'left'
          ? 'lg:after:absolute lg:after:top-1/2 lg:after:left-full lg:after:w-8 lg:after:border-t-2 lg:after:border-dashed lg:after:border-brand/40'
          : 'lg:before:absolute lg:before:top-1/2 lg:before:right-full lg:before:w-8 lg:before:border-t-2 lg:before:border-dashed lg:before:border-brand/40')
      }
    >
      <div className="flex items-center gap-3">
        <span className="grid size-9 shrink-0 place-items-center rounded-xl bg-accent text-primary">
          <Icon className="size-5" />
        </span>
        <div className="min-w-0">
          <h3 className="font-bold leading-snug">{role.name}</h3>
          <p className="text-xs text-ink-3">{role.via}</p>
        </div>
      </div>
      <ul className="mt-3 space-y-1 text-sm text-ink-2">
        {role.points.map((p) => (
          <li key={p} className="flex gap-2">
            <span className="text-brand" aria-hidden="true">•</span>
            <span>{p}</span>
          </li>
        ))}
      </ul>
      {role.tools.length > 0 && (
        <p className="mt-3 flex flex-wrap gap-1.5">
          {role.tools.map((t) => (
            <code key={t} className="rounded-md bg-[#131c33] px-1.5 py-0.5 font-mono text-[11px] text-[#8fb4ff]">
              {t}
            </code>
          ))}
        </p>
      )}
    </div>
  )
}

/** エージェントの仕組み: 4 つの役割が、中央の承認済みの知識と検査器を囲む図。 */
export function AgentSystem() {
  const [coding, writer, investigator, a2a] = AGENT_ROLES
  const s = AGENTS_SECTION
  return (
    <div className="mt-8 grid items-center gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,16rem)_minmax(0,1fr)] lg:gap-x-8">
      <div className="grid gap-4">
        <RoleCard role={coding} side="left" />
        <RoleCard role={a2a} side="left" />
      </div>
      <div className="grid gap-4">
        <div className="rounded-2xl bg-[#131c33] p-5 text-center text-[#e8ecf8] shadow-lift ring-1 ring-white/10">
          <ShieldCheck className="mx-auto size-8 text-[#4fd1a8]" aria-hidden="true" />
          <p className="mt-2 text-lg font-extrabold">{s.hubTitle}</p>
          <ul className="mt-2 space-y-1 text-sm text-[#c5cde3]">
            {s.hubPoints.map((p) => (
              <li key={p}>{p}</li>
            ))}
          </ul>
          <p className="mt-3 rounded-lg bg-white/10 px-2 py-1 text-xs text-[#4fd1a8]">{s.hubNote}</p>
        </div>
        <div className="rounded-2xl border-2 border-dashed border-brand/30 p-4 text-center">
          <UserCheck className="mx-auto size-6 text-primary" aria-hidden="true" />
          <p className="mt-1 font-bold">{s.human.title}</p>
          <p className="text-xs text-ink-2">{s.human.body}</p>
        </div>
      </div>
      <div className="grid gap-4">
        <RoleCard role={writer} side="right" />
        <RoleCard role={investigator} side="right" />
      </div>
    </div>
  )
}
