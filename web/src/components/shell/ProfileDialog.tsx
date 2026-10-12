import { useState, type FormEvent } from 'react'
import { toast } from 'sonner'
import { updateMyProfile } from '@/api/me'
import { ApiError } from '@/api/errors'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { PROFILE_LABELS } from '@/services/labels'
import { DISPLAY_NAME_MAX_LENGTH, parseDisplayName } from '@/services/profile'

/** 本人の表示名を変えるダイアログ。空にすると表示名を消し、メール（無ければ ID の短縮）で表示される。 */
export function ProfileDialog({
  open,
  current,
  onOpenChange,
  onSaved,
}: {
  open: boolean
  current: string | null
  onOpenChange: (open: boolean) => void
  onSaved: () => void
}) {
  // 閉じている間はフォームを描画しない。開くたびに今の表示名から始まる
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        {open && <ProfileForm current={current} onDone={() => onOpenChange(false)} onSaved={onSaved} />}
      </DialogContent>
    </Dialog>
  )
}

function ProfileForm({ current, onDone, onSaved }: { current: string | null; onDone: () => void; onSaved: () => void }) {
  const [value, setValue] = useState(current ?? '')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  function handleSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault()
    const parsed = parseDisplayName(value)
    if (!parsed.ok) {
      setError(parsed.message)
      return
    }
    setSaving(true)
    setError(null)
    updateMyProfile(parsed.value)
      .then(() => {
        toast.success(parsed.value ? `${PROFILE_LABELS.displayName}を「${parsed.value}」にしました` : `${PROFILE_LABELS.displayName}を消しました`)
        onSaved()
        onDone()
      })
      .catch((err: unknown) => setError(err instanceof ApiError ? err.message : `${PROFILE_LABELS.displayName}を保存できませんでした`))
      .finally(() => setSaving(false))
  }

  return (
    <form onSubmit={handleSubmit} className="grid gap-4">
      <DialogHeader>
        <DialogTitle>{PROFILE_LABELS.editDisplayName}</DialogTitle>
        <DialogDescription>
          同じワークスペースのメンバーに、メンバーの一覧や改訂の作者・承認者として、メールアドレスと並べて表示されます。@ は使えません。空にするとメールアドレスで表示されます。
        </DialogDescription>
      </DialogHeader>
      <div className="grid gap-1.5">
        <Label htmlFor="display-name">{PROFILE_LABELS.displayName}</Label>
        <Input
          id="display-name"
          value={value}
          onChange={(e) => setValue(e.target.value)}
          maxLength={DISPLAY_NAME_MAX_LENGTH * 2}
          autoComplete="nickname"
          autoFocus
        />
        <p className="text-xs text-ink-3">{DISPLAY_NAME_MAX_LENGTH} 文字まで</p>
        {error && (
          <p role="alert" className="text-sm text-bad">
            {error}
          </p>
        )}
      </div>
      <DialogFooter>
        <DialogClose asChild>
          <Button type="button" variant="secondary">
            やめる
          </Button>
        </DialogClose>
        <Button type="submit" disabled={saving} className="bg-brand text-white">
          {saving ? '保存しています…' : '保存'}
        </Button>
      </DialogFooter>
    </form>
  )
}
