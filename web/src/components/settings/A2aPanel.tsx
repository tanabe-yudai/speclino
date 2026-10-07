import { Copy } from 'lucide-react'
import { toast } from 'sonner'
import { Panel, PanelTitle } from '@/components/app/Panel'
import { Button } from '@/components/ui/button'
import { agentCardUrl } from '@/services/a2a'

/** 他のエージェントが A2A で接続するための Agent Card URL を示す（S60）。 */
export function A2aPanel({ origin }: { origin: string }) {
  const url = agentCardUrl(origin)

  async function copyUrl() {
    try {
      await navigator.clipboard.writeText(url)
      toast.success('コピーしました')
    } catch {
      toast.error('コピーできませんでした。選択してコピーしてください')
    }
  }

  return (
    <Panel>
      <PanelTitle>エージェントから相談する（A2A）</PanelTitle>
      <p className="mt-1 text-sm text-ink-2">
        他のエージェントも、上で発行したエージェント接続トークンで SpecLino に相談できます。接続先は次の URL です。
      </p>
      <div className="mt-3 flex items-center gap-2">
        <code className="min-w-0 flex-1 rounded-md bg-muted px-3 py-2 font-mono text-xs break-all select-all">
          {url}
        </code>
        <Button variant="secondary" size="sm" onClick={copyUrl}>
          <Copy className="size-3.5" /> コピー
        </Button>
      </div>
    </Panel>
  )
}
