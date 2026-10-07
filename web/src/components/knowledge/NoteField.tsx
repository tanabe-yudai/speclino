import { useState } from 'react'
import { Button } from '@/components/ui/button'
import { Label } from '@/components/ui/label'
import { Textarea } from '@/components/ui/textarea'
import { NOTE_MAX_CHARS, editNote, noteFromSummary } from '@/services/knowledgeChat'
import type { ChatSummaryDraft, NoteState } from '@/types/knowledgeChat'

/**
 * 改訂の説明（承認者が読む「変更の説明」）。「会話の要約を使う」は AI が会話から作った案を入れるだけで、
 * 本人が確かめて直してから保存する（自動では入れない、設計 C3）。
 */
export function NoteField({
  id,
  note,
  onChange,
  summarize,
}: {
  id: string
  note: NoteState
  onChange: (note: NoteState) => void
  /** 要約できる会話があるときだけ渡す */
  summarize?: () => Promise<ChatSummaryDraft | null>
}) {
  const [pending, setPending] = useState(false)

  function applySummary() {
    if (!summarize) return
    setPending(true)
    summarize()
      .then((draft) => draft && onChange(noteFromSummary(draft)))
      .finally(() => setPending(false))
  }

  return (
    <div className="grid w-full gap-1.5">
      <div className="flex flex-wrap items-center gap-2">
        <Label htmlFor={id}>変更の説明（任意）</Label>
        {summarize && (
          <Button type="button" size="sm" variant="outline" onClick={applySummary} disabled={pending}>
            {pending ? '要約を作っています…' : '会話の要約を使う'}
          </Button>
        )}
        {note.source === 'chat_summary' && note.text.trim() && <span className="text-xs text-ink-3">会話の要約から入れました</span>}
      </div>
      <Textarea
        id={id}
        value={note.text}
        onChange={(e) => onChange(editNote(note, e.target.value))}
        rows={3}
        maxLength={NOTE_MAX_CHARS}
        aria-describedby={`${id}-help`}
      />
      <p id={`${id}-help`} className="text-xs text-ink-3">
        承認者が読みます（{NOTE_MAX_CHARS} 文字まで）。AI の要約は、見せたくない内容が無いか確かめてから保存してください。
      </p>
    </div>
  )
}
