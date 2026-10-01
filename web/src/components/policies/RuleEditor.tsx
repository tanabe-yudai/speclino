import { useState } from 'react'
import { Panel, PanelTitle } from '@/components/app/Panel'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Textarea } from '@/components/ui/textarea'
import { ON_MISSING_INPUT_LABELS, POLICY_LEVEL_LABELS } from '@/services/labels'
import { newRule, parsePaths, ruleProblems, withVerifier } from '@/services/policyDraft'
import type { OnMissingInput, PolicyLevel, PolicyRule } from '@/types/policy'

const LEVELS: PolicyLevel[] = ['advisory', 'mandatory']
const ON_MISSING: OnMissingInput[] = ['unknown', 'violation', 'skipped']
const SELECT_CLASS = 'h-9 w-full rounded-md border bg-card px-3 text-sm'

/** ルールの編集フォーム（design 6 章）。保存は下書きに入る。rule が null なら追加。 */
export function RuleEditor({
  rule,
  existingIds,
  verifiers,
  saving,
  onCancel,
  onSave,
  onDelete,
}: {
  rule: PolicyRule | null
  existingIds: string[]
  verifiers: Record<string, string>
  saving: boolean
  onCancel: () => void
  onSave: (rule: PolicyRule, originalId: string | undefined) => void
  onDelete: (ruleId: string) => void
}) {
  const originalId = rule?.id
  const [draft, setDraft] = useState<PolicyRule>(rule ?? newRule(''))
  const [paths, setPaths] = useState((rule?.applies_to.paths ?? []).join('\n'))
  const current: PolicyRule = { ...draft, id: draft.id.trim(), applies_to: { ...draft.applies_to, paths: parsePaths(paths) } }
  const problems = ruleProblems(current, existingIds, originalId)
  const set = (patch: Partial<PolicyRule>) => setDraft({ ...draft, ...patch })

  return (
    <Panel className="space-y-4">
      <PanelTitle>{rule ? `${rule.id} を編集` : 'ルールを追加'}</PanelTitle>
      <form
        className="space-y-4"
        onSubmit={(e) => {
          e.preventDefault()
          if (problems.length === 0) onSave(current, originalId)
        }}
      >
        <div className="grid gap-4 sm:grid-cols-2">
          <div className="space-y-1.5">
            <Label htmlFor="rule-id">ID</Label>
            <Input id="rule-id" value={draft.id} onChange={(e) => set({ id: e.target.value })} disabled={rule !== null} />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="rule-title">題</Label>
            <Input id="rule-title" value={draft.title} onChange={(e) => set({ title: e.target.value })} />
          </div>
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="rule-text">本文</Label>
          <Textarea id="rule-text" value={draft.text} onChange={(e) => set({ text: e.target.value })} rows={3} />
        </div>
        <fieldset className="space-y-1.5">
          <legend className="text-sm font-medium">レベル</legend>
          <div className="flex gap-4">
            {LEVELS.map((l) => (
              <label key={l} className="flex items-center gap-1.5 text-sm">
                <input type="radio" name="rule-level" checked={draft.level === l} onChange={() => set({ level: l })} />
                {POLICY_LEVEL_LABELS[l]}
              </label>
            ))}
          </div>
        </fieldset>
        <div className="grid gap-4 sm:grid-cols-2">
          <div className="space-y-1.5">
            <Label htmlFor="rule-verifier">検査器</Label>
            <select
              id="rule-verifier"
              className={SELECT_CLASS}
              value={draft.verifier?.id ?? ''}
              onChange={(e) => setDraft(withVerifier(draft, e.target.value, verifiers))}
            >
              <option value="">なし（助言のみ）</option>
              {Object.keys(verifiers).map((id) => (
                <option key={id} value={id}>
                  {id}
                </option>
              ))}
            </select>
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="rule-missing">対象が無いとき</Label>
            <select
              id="rule-missing"
              className={SELECT_CLASS}
              value={draft.on_missing_input}
              onChange={(e) => set({ on_missing_input: e.target.value as OnMissingInput })}
            >
              {ON_MISSING.map((o) => (
                <option key={o} value={o}>
                  {ON_MISSING_INPUT_LABELS[o]}
                </option>
              ))}
            </select>
          </div>
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="rule-paths">適用範囲（glob、改行かカンマで区切る）</Label>
          <Textarea id="rule-paths" value={paths} onChange={(e) => setPaths(e.target.value)} rows={2} className="font-mono text-xs" />
        </div>
        <label className="flex items-center gap-2 text-sm">
          <input
            type="checkbox"
            checked={draft.exceptions_allowed}
            onChange={(e) => set({ exceptions_allowed: e.target.checked })}
          />
          例外（.speclino/exceptions.yaml）を認める
        </label>
        {problems.length > 0 && (
          <ul className="space-y-0.5 text-sm text-bad">
            {problems.map((p) => (
              <li key={p}>{p}</li>
            ))}
          </ul>
        )}
        <div className="flex flex-wrap gap-2">
          <Button type="submit" disabled={problems.length > 0 || saving}>
            下書きに保存
          </Button>
          <Button type="button" variant="secondary" onClick={onCancel}>
            やめる
          </Button>
          {rule && (
            <Button type="button" variant="destructive" className="ml-auto" disabled={saving} onClick={() => onDelete(rule.id)}>
              このルールを下書きから削除
            </Button>
          )}
        </div>
      </form>
    </Panel>
  )
}
