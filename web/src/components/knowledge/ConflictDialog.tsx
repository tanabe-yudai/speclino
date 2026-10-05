import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import { Dialog, DialogClose, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import type { ConflictDialogProps } from '@/types/specEditor'

/** 409 draft_changed: 他の人が先に保存・承認した。読み直すと自分の変更は消えるので、先にコピーできるようにする。 */
export function ConflictDialog({ open, ownText, onReload, onClose }: ConflictDialogProps) {
  async function copyThenReload() {
    try {
      await navigator.clipboard.writeText(ownText)
      toast.success('あなたの本文をコピーしました')
    } catch {
      toast.error('コピーできませんでした。本文を選択してコピーしてから読み直してください')
      return
    }
    onReload()
  }
  return (
    <Dialog open={open} onOpenChange={(o) => !o && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>他の人が先に保存または承認しました</DialogTitle>
          <DialogDescription>
            あなたが見ていた改訂から内容が変わっています。{ownText ? '読み直すと、まだ保存していないあなたの変更は消えます。' : '読み直してから、もう一度確かめてください。'}
          </DialogDescription>
        </DialogHeader>
        <DialogFooter>
          <DialogClose asChild>
            <Button variant="secondary">やめる</Button>
          </DialogClose>
          {ownText && (
            <Button variant="secondary" onClick={copyThenReload}>
              本文をコピーしてから読み直す
            </Button>
          )}
          <Button onClick={onReload}>読み直す</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
