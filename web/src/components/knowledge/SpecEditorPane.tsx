import { useRef, useState } from 'react'
import { CanonicalView } from '@/components/spec-editor/CanonicalView'
import { DiagnosticsList } from '@/components/spec-editor/DiagnosticsList'
import { MarkdownInput } from '@/components/spec-editor/MarkdownInput'
import { MermaidPreview } from '@/components/spec-editor/MermaidPreview'
import { RequirementTrace } from '@/components/spec-editor/RequirementTrace'
import { SuggestionPanel } from '@/components/spec-editor/SuggestionPanel'
import { Label } from '@/components/ui/label'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { useSuggestions } from '@/hooks/useSuggestions'
import { focusLine } from '@/lib/textarea'
import { countErrors, parseJsonField, requirementTrace } from '@/services/specPreview'
import type { DomainSpecJson, SpecIrJson } from '@/types/knowledge'
import type { PaneValidity, SpecPaneProps } from '@/types/specEditor'

const pretty = (v: unknown) => (v === null || (Array.isArray(v) && v.length === 0) ? '' : JSON.stringify(v, null, 2))

/** 仕様エディタ（設計 9 章）: 左に本文、右に図・正規形・診断・要求の追跡。 */
export function SpecEditorPane({ slug, content, onChange, preview, previewError, previewPending, saving, onInvalidChange, resetKey }: SpecPaneProps & PaneValidity) {
  const textarea = useRef<HTMLTextAreaElement>(null)
  const [tab, setTab] = useState('diagnostics')
  const [domainText, setDomainText] = useState(() => pretty(content.domain))
  const [legacyText, setLegacyText] = useState(() => pretty(content.legacy_ir))
  // 保存の応答で本文がサーバの内容に揃ったら、JSON 欄もそれに合わせて作り直す（編集中は揃えないので上書きしない）
  const [seenReset, setSeenReset] = useState(resetKey)
  if (seenReset !== resetKey) {
    setSeenReset(resetKey)
    setDomainText(pretty(content.domain))
    setLegacyText(pretty(content.legacy_ir))
  }
  const domain = parseJsonField(domainText, 'object')
  const legacy = parseJsonField(legacyText, 'array')

  const diagnostics = preview?.parse_result.diagnostics ?? []
  const errors = countErrors(diagnostics)
  const compiled = preview?.parse_result.compiled ?? null
  const trace = requirementTrace(compiled)

  // タブを切り替えても提案を失わないよう、状態はここ（常に表示される親）で持つ
  const suggestions = useSuggestions(slug, content.markdown, (markdown) => onChange({ ...content, markdown }))
  const suggestBlocked = saving
    ? '保存中は提案を求められません。'
    : previewPending
      ? '診断を更新しています。終わってから提案を求めてください。'
      : domain.error || legacy.error
        ? '旧形式の入口の JSON が読めないため、提案を求められません。'
        : !content.markdown.trim()
          ? '本文を入力すると提案を求められます。'
          : null

  function jump(line: number, column: number) {
    if (textarea.current) focusLine(textarea.current, line, column)
  }

  function updateJson(field: 'domain' | 'legacy', text: string) {
    const nextDomain = field === 'domain' ? parseJsonField(text, 'object') : domain
    const nextLegacy = field === 'legacy' ? parseJsonField(text, 'array') : legacy
    if (field === 'domain') setDomainText(text)
    else setLegacyText(text)
    const invalid = !!(nextDomain.error || nextLegacy.error)
    onInvalidChange(invalid)
    if (!invalid) {
      onChange({ ...content, domain: nextDomain.value as DomainSpecJson | null, legacy_ir: nextLegacy.value as SpecIrJson[] })
    }
  }

  return (
    <div className="grid gap-6 lg:grid-cols-2">
      <div className="min-w-0 space-y-4">
        <MarkdownInput
          id="spec-markdown"
          label="仕様の本文（Markdown と ```mermaid の図）"
          value={content.markdown}
          onChange={(markdown) => onChange({ ...content, markdown })}
          textareaRef={textarea}
          describedBy="spec-markdown-help"
          invalid={errors > 0}
        />
        <p id="spec-markdown-help" className="text-xs text-ink-3">
          図には <code className="font-mono">%%@ id:</code> などの宣言と要素の ID が要ります。書き方は parser の文法（grammar.md）に従います。
        </p>
        <details className="rounded-md border p-3" open={!!(domainText || legacyText)}>
          <summary className="cursor-pointer text-sm font-medium">旧形式の入口（domain と Spec IR の JSON）</summary>
          <div className="mt-3 space-y-3">
            <div className="grid gap-1.5">
              <Label htmlFor="spec-domain">domain（JSON のオブジェクト。無ければ空）</Label>
              <textarea
                id="spec-domain"
                className="min-h-24 rounded-lg border border-input bg-transparent px-3 py-2 font-mono text-xs aria-invalid:border-destructive dark:bg-input/30"
                value={domainText}
                onChange={(e) => updateJson('domain', e.target.value)}
                aria-invalid={!!domain.error || undefined}
                aria-describedby={domain.error ? 'spec-domain-error' : undefined}
                spellCheck={false}
              />
              {domain.error && (
                <p id="spec-domain-error" className="text-sm text-bad">
                  {domain.error}
                </p>
              )}
            </div>
            <div className="grid gap-1.5">
              <Label htmlFor="spec-legacy">Spec IR（JSON の配列。無ければ空）</Label>
              <textarea
                id="spec-legacy"
                className="min-h-24 rounded-lg border border-input bg-transparent px-3 py-2 font-mono text-xs aria-invalid:border-destructive dark:bg-input/30"
                value={legacyText}
                onChange={(e) => updateJson('legacy', e.target.value)}
                aria-invalid={!!legacy.error || undefined}
                aria-describedby={legacy.error ? 'spec-legacy-error' : undefined}
                spellCheck={false}
              />
              {legacy.error && (
                <p id="spec-legacy-error" className="text-sm text-bad">
                  {legacy.error}
                </p>
              )}
            </div>
          </div>
        </details>
      </div>
      <div className="min-w-0 space-y-3">
        <div aria-live="polite" className="text-xs text-ink-3">
          {previewPending ? '診断を更新しています…' : preview ? `エラー ${errors} 件 · 警告 ${diagnostics.length - errors} 件` : ''}
        </div>
        {previewError && (
          <p role="alert" className="text-sm text-bad">
            {previewError}
          </p>
        )}
        <Tabs value={tab} onValueChange={setTab}>
          <div className="-mx-1 overflow-x-auto px-1">
            <TabsList>
              <TabsTrigger value="diagnostics">診断{preview ? `（${diagnostics.length}）` : ''}</TabsTrigger>
              <TabsTrigger value="diagrams">図</TabsTrigger>
              <TabsTrigger value="canonical">正規形</TabsTrigger>
              <TabsTrigger value="trace">要求の追跡</TabsTrigger>
              <TabsTrigger value="suggest">AI の提案</TabsTrigger>
            </TabsList>
          </div>
          <TabsContent value="diagnostics" className="mt-3">
            {preview ? <DiagnosticsList diagnostics={diagnostics} onJump={jump} /> : <p className="text-sm text-ink-3">本文を入力すると診断が出ます。</p>}
          </TabsContent>
          <TabsContent value="diagrams" className="mt-3">
            <MermaidPreview markdown={content.markdown} onJump={jump} />
          </TabsContent>
          <TabsContent value="canonical" className="mt-3">
            <CanonicalView canonical={preview?.canonical ?? null} pending={previewPending} hasErrors={errors > 0} />
          </TabsContent>
          <TabsContent value="trace" className="mt-3">
            <RequirementTrace trace={trace} hasCompiled={compiled !== null} />
          </TabsContent>
          <TabsContent value="suggest" className="mt-3">
            <SuggestionPanel
              state={suggestions.state}
              stale={suggestions.stale}
              blockedReason={suggestBlocked}
              onRequest={() => suggestions.request({ markdown: content.markdown, domain: content.domain, legacy_ir: content.legacy_ir })}
              onApply={suggestions.apply}
              onReject={suggestions.reject}
            />
          </TabsContent>
        </Tabs>
      </div>
    </div>
  )
}
