import { scopeProblem } from '@/services/knowledge'
import type { ScopePickerProps } from '@/types/specEditor'

/** スコープ（ワークスペース全体 / プロジェクトの複数選択）。仕様・スキル・指示書で共通。 */
export function ScopePicker({ scope, onChange, projects, error }: ScopePickerProps) {
  const problem = scopeProblem(scope)
  const toggle = (id: string, checked: boolean) =>
    onChange({ workspace: false, projects: checked ? [...scope.projects, id] : scope.projects.filter((p) => p !== id) })

  return (
    <fieldset className="space-y-2" aria-describedby={problem ? 'scope-problem' : undefined}>
      <legend className="text-sm font-medium">スコープ</legend>
      <div className="flex flex-wrap gap-4 text-sm">
        <label className="flex items-center gap-2">
          <input type="radio" name="scope-form" checked={scope.workspace} onChange={() => onChange({ workspace: true, projects: [] })} />
          ワークスペース全体
        </label>
        <label className="flex items-center gap-2">
          <input type="radio" name="scope-form" checked={!scope.workspace} onChange={() => onChange({ workspace: false, projects: scope.projects })} />
          プロジェクトを選ぶ
        </label>
      </div>
      {!scope.workspace && (
        <div className="space-y-1">
          {error && <p className="text-sm text-bad">{error}</p>}
          {projects === null && !error && <p className="text-sm text-ink-3">プロジェクトを取得しています…</p>}
          {projects?.length === 0 && <p className="text-sm text-ink-3">このワークスペースにはプロジェクトがありません。</p>}
          {projects?.map((p) => (
            <label key={p.id} className="flex items-center gap-2 text-sm">
              <input type="checkbox" checked={scope.projects.includes(p.id)} onChange={(e) => toggle(p.id, e.target.checked)} />
              <span className="font-mono text-xs break-all">{p.repo_full_name}</span>
            </label>
          ))}
          {/* 一覧に無いプロジェクト（他のワークスペースへ移ったもの等）も外せるように出す */}
          {scope.projects
            .filter((id) => projects && !projects.some((p) => p.id === id))
            .map((id) => (
              <label key={id} className="flex items-center gap-2 text-sm text-ink-3">
                <input type="checkbox" checked onChange={() => toggle(id, false)} />
                <span className="font-mono text-xs break-all">{id}（このワークスペースに無いプロジェクト）</span>
              </label>
            ))}
        </div>
      )}
      {problem && (
        <p id="scope-problem" className="text-sm text-bad">
          {problem}
        </p>
      )}
    </fieldset>
  )
}
