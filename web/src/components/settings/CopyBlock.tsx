import { Copy } from 'lucide-react'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'

/** コピーできる手順・URL のブロック（秘密は入れない）。 */
export function CopyBlock({ text, label }: { text: string; label: string }) {
  async function copy() {
    try {
      await navigator.clipboard.writeText(text)
      toast.success('コピーしました')
    } catch {
      toast.error('コピーできませんでした。選択してコピーしてください')
    }
  }
  return (
    <div className="flex items-start gap-2">
      <pre aria-label={label} className="min-w-0 flex-1 overflow-x-auto rounded-md bg-muted px-3 py-2 font-mono text-xs whitespace-pre-wrap break-all select-all">
        {text}
      </pre>
      <Button type="button" variant="secondary" size="sm" onClick={copy}>
        <Copy className="size-3.5" /> コピー
      </Button>
    </div>
  )
}
