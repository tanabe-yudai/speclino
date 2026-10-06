import { useState } from 'react'
import { MarkdownInput } from '@/components/spec-editor/MarkdownInput'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { checkSkillFrontmatter, checkSkillPath } from '@/services/skillFrontmatter'
import type { SkillContent } from '@/types/knowledge'
import type { ContentEditorProps, PaneValidity } from '@/types/specEditor'

interface FileEntry {
  key: number
  path: string
  body: string
}

let nextKey = 0
const toEntries = (files: Record<string, string>): FileEntry[] =>
  Object.entries(files).map(([path, body]) => ({ key: nextKey++, path, body }))

function problemsOf(entries: FileEntry[]): (string | null)[] {
  return entries.map((e, i) => checkSkillPath(e.path, entries.filter((_, k) => k !== i).map((o) => o.path)))
}

/** スキル（SKILL.md と付属ファイル）。frontmatter とパスは画面で先に確かめ、正はサーバの検証。 */
export function SkillEditorPane({ content, onChange, onInvalidChange, resetKey }: ContentEditorProps<SkillContent> & PaneValidity) {
  const [entries, setEntries] = useState<FileEntry[]>(() => toEntries(content.files))
  // 保存の応答で本文がサーバの内容に揃ったら、付属ファイルの欄も作り直す
  const [seenReset, setSeenReset] = useState(resetKey)
  if (seenReset !== resetKey) {
    setSeenReset(resetKey)
    setEntries(toEntries(content.files))
  }
  const frontmatter = checkSkillFrontmatter(content.skill_md)
  const pathProblems = problemsOf(entries)

  function updateEntries(next: FileEntry[]) {
    setEntries(next)
    const invalid = problemsOf(next).some(Boolean)
    onInvalidChange(invalid)
    if (!invalid) onChange({ ...content, files: Object.fromEntries(next.map((e) => [e.path, e.body])) })
  }

  return (
    <div className="space-y-6">
      <div className="space-y-2">
        <MarkdownInput
          id="skill-md"
          label="SKILL.md"
          value={content.skill_md}
          onChange={(skill_md) => onChange({ ...content, skill_md })}
          describedBy="skill-md-check"
          invalid={frontmatter.problems.length > 0}
          rows={20}
        />
        <div id="skill-md-check" className="text-sm">
          {frontmatter.problems.length === 0 ? (
            <p className="text-ink-3">
              name: <span className="font-mono">{frontmatter.name}</span> · description: {frontmatter.description}
            </p>
          ) : (
            <ul className="list-disc space-y-0.5 pl-5 text-bad">
              {frontmatter.problems.map((p) => (
                <li key={p}>{p}</li>
              ))}
            </ul>
          )}
        </div>
      </div>
      <section className="space-y-3" aria-labelledby="skill-files-title">
        <div className="flex items-center gap-3">
          <h3 id="skill-files-title" className="text-sm font-semibold">
            付属ファイル（{entries.length}）
          </h3>
          <Button type="button" variant="secondary" size="sm" onClick={() => updateEntries([...entries, { key: nextKey++, path: '', body: '' }])}>
            ファイルを追加
          </Button>
        </div>
        {entries.length === 0 && <p className="text-sm text-ink-3">付属ファイルはありません（テキストのみ、1 ファイル 256KB・合計 1MB まで）。</p>}
        {entries.map((entry, i) => {
          const problem = pathProblems[i]
          const pathId = `skill-file-path-${entry.key}`
          return (
            <div key={entry.key} className="space-y-2 rounded-md border p-3">
              <div className="flex flex-wrap items-end gap-2">
                <div className="grid min-w-[14rem] flex-1 gap-1.5">
                  <Label htmlFor={pathId}>相対パス</Label>
                  <Input
                    id={pathId}
                    value={entry.path}
                    placeholder="例: scripts/check.sh"
                    aria-invalid={!!problem || undefined}
                    aria-describedby={problem ? `${pathId}-error` : undefined}
                    onChange={(e) => updateEntries(entries.map((x) => (x.key === entry.key ? { ...x, path: e.target.value } : x)))}
                  />
                </div>
                <Button type="button" variant="ghost" size="sm" className="text-bad" onClick={() => updateEntries(entries.filter((x) => x.key !== entry.key))}>
                  削除
                </Button>
              </div>
              {problem && (
                <p id={`${pathId}-error`} className="text-sm text-bad">
                  {problem}
                </p>
              )}
              <MarkdownInput
                id={`skill-file-body-${entry.key}`}
                label={`${entry.path || '（パス未入力）'} の本文`}
                value={entry.body}
                rows={8}
                onChange={(body) => updateEntries(entries.map((x) => (x.key === entry.key ? { ...x, body } : x)))}
              />
            </div>
          )
        })}
      </section>
    </div>
  )
}
