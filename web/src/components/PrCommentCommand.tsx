import { toast } from 'sonner'
import { Button } from '@/components/ui/button'

/** PR のコメントで確定するときのコマンド。ふだんは畳んでおき、必要な人だけがコピーする。 */
export function PrCommentCommand({ command }: { command: string }) {
  async function copy() {
    try {
      await navigator.clipboard.writeText(command)
      toast.success('コピーしました')
    } catch {
      toast.error('コピーできませんでした。選択してコピーしてください')
    }
  }
  return (
    <details className="text-xs">
      <summary className="cursor-pointer text-ink-3">PR のコメントで確定するには</summary>
      <div className="mt-1 flex flex-wrap items-center gap-2">
        <code className="min-w-0 rounded bg-muted px-2 py-1 font-mono break-all">{command}</code>
        <Button type="button" size="sm" variant="secondary" onClick={copy}>
          コピー
        </Button>
      </div>
    </details>
  )
}
