import { SAMPLE_MCP } from '@/services/landingContent'
import { Sample } from './Sample'

export function SampleMcp() {
  return (
    <Sample label="エディタに SpecLino の MCP を追加し、エージェントが承認済みの知識を書き出す見本">
      <div className="rounded-2xl bg-[#131c33] p-4 font-mono text-xs leading-relaxed text-[#e8ecf8] shadow-lift">
        <p className="break-all">$ {SAMPLE_MCP.command}</p>
        {SAMPLE_MCP.responses.map((r, i) => (
          <p key={r} className={i === SAMPLE_MCP.responses.length - 1 ? 'mt-1 text-[#4fd1a8]' : 'mt-1 text-[#9aa6c4]'}>
            › {r}
          </p>
        ))}
      </div>
    </Sample>
  )
}
