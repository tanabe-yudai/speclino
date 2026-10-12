import { useRef, useState } from "react";
import { flushSync } from "react-dom";
import { PaneViewSwitch } from "@/components/knowledge/PaneViewSwitch";
import { DiagnosticsList } from "@/components/spec-editor/DiagnosticsList";
import { MarkdownInput } from "@/components/spec-editor/MarkdownInput";
import { Label } from "@/components/ui/label";
import { MarkdownPreview } from "@/components/knowledge/MarkdownPreview";
import { cn } from "@/lib/utils";
import { focusLine } from "@/lib/textarea";
import { initialPaneView } from "@/services/paneView";
import { countErrors, parseJsonField } from "@/services/specPreview";
import type { DomainSpecJson, SpecIrJson } from "@/types/knowledge";
import type { PaneView } from "@/types/paneView";
import type { PaneValidity, SpecPaneProps } from "@/types/specEditor";

const pretty = (v: unknown) =>
  v === null || (Array.isArray(v) && v.length === 0)
    ? ""
    : JSON.stringify(v, null, 2);

/** 仕様エディタ: 左に本文、右に描いた本文（図つき）と診断の件数。 */
export function SpecEditorPane({
  content,
  onChange,
  preview,
  previewError,
  previewPending,
  onInvalidChange,
  resetKey,
}: SpecPaneProps & PaneValidity) {
  const textarea = useRef<HTMLTextAreaElement>(null);
  const [view, setView] = useState<PaneView>(() =>
    initialPaneView(content.markdown),
  );
  const [domainText, setDomainText] = useState(() => pretty(content.domain));
  const [legacyText, setLegacyText] = useState(() => pretty(content.legacy_ir));
  // 取り込んだ要求がある仕様だけ、その JSON 欄を見せる（新しい仕様には出さない）
  const [hasLegacy, setHasLegacy] = useState(
    () => !!(pretty(content.domain) || pretty(content.legacy_ir)),
  );
  // 保存の応答で本文がサーバの内容に揃ったら、JSON 欄もそれに合わせて作り直す（編集中は揃えないので上書きしない）
  const [seenReset, setSeenReset] = useState(resetKey);
  if (seenReset !== resetKey) {
    setSeenReset(resetKey);
    setDomainText(pretty(content.domain));
    setLegacyText(pretty(content.legacy_ir));
    setHasLegacy(!!(pretty(content.domain) || pretty(content.legacy_ir)));
  }
  const domain = parseJsonField(domainText, "object");
  const legacy = parseJsonField(legacyText, "array");

  const diagnostics = preview?.parse_result.diagnostics ?? [];
  const errors = countErrors(diagnostics);
  function jump(line: number, column: number) {
    // 本文が隠れている（狭い画面のプレビュー）と focus できないので、先に本文を出す
    flushSync(() => setView("edit"));
    if (textarea.current) focusLine(textarea.current, line, column);
  }

  function updateJson(field: "domain" | "legacy", text: string) {
    const nextDomain =
      field === "domain" ? parseJsonField(text, "object") : domain;
    const nextLegacy =
      field === "legacy" ? parseJsonField(text, "array") : legacy;
    if (field === "domain") setDomainText(text);
    else setLegacyText(text);
    const invalid = !!(nextDomain.error || nextLegacy.error);
    onInvalidChange(invalid);
    if (!invalid) {
      onChange({
        ...content,
        domain: nextDomain.value as DomainSpecJson | null,
        legacy_ir: nextLegacy.value as SpecIrJson[],
      });
    }
  }

  return (
    <div className="space-y-4 lg:space-y-0">
      <PaneViewSwitch
        view={view}
        onChange={setView}
        diagnosticCount={preview ? diagnostics.length : null}
      />
      <div className="grid gap-6 lg:grid-cols-2">
        <div
          className={cn(
            "min-w-0 space-y-4",
            view !== "edit" && "hidden lg:block",
          )}
        >
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
            図の書き方は右の診断で確かめられます。
          </p>
          {hasLegacy && (
            <details className="rounded-md border p-3" open>
              <summary className="cursor-pointer text-sm font-medium">
                取り込んだ要求（JSON）
              </summary>
              <div className="mt-3 space-y-3">
                <div className="grid gap-1.5">
                  <Label htmlFor="spec-domain">対象の定義（JSON）</Label>
                  <textarea
                    id="spec-domain"
                    className="min-h-24 rounded-lg border border-input bg-transparent px-3 py-2 font-mono text-xs aria-invalid:border-destructive dark:bg-input/30"
                    value={domainText}
                    onChange={(e) => updateJson("domain", e.target.value)}
                    aria-invalid={!!domain.error || undefined}
                    aria-describedby={
                      domain.error ? "spec-domain-error" : undefined
                    }
                    spellCheck={false}
                  />
                  {domain.error && (
                    <p id="spec-domain-error" className="text-sm text-bad">
                      {domain.error}
                    </p>
                  )}
                </div>
                <div className="grid gap-1.5">
                  <Label htmlFor="spec-legacy">取り込んだ要求（JSON）</Label>
                  <textarea
                    id="spec-legacy"
                    className="min-h-24 rounded-lg border border-input bg-transparent px-3 py-2 font-mono text-xs aria-invalid:border-destructive dark:bg-input/30"
                    value={legacyText}
                    onChange={(e) => updateJson("legacy", e.target.value)}
                    aria-invalid={!!legacy.error || undefined}
                    aria-describedby={
                      legacy.error ? "spec-legacy-error" : undefined
                    }
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
          )}
        </div>
        <div
          className={cn(
            "min-w-0 space-y-3",
            view !== "preview" && "hidden lg:block",
          )}
        >
          <div aria-live="polite" className="text-xs text-ink-3">
            {previewPending
              ? "診断を更新しています…"
              : preview
                ? `エラー ${errors} 件 · 警告 ${diagnostics.length - errors} 件`
                : ""}
          </div>
          {previewError && (
            <p role="alert" className="text-sm text-bad">
              {previewError}
            </p>
          )}
          {preview &&
            (diagnostics.length > 0 ? (
              <details className="rounded-md border p-3">
                <summary className="cursor-pointer text-sm font-medium">
                  診断の内容（{diagnostics.length} 件）
                </summary>
                <div className="mt-3">
                  <DiagnosticsList diagnostics={diagnostics} onJump={jump} />
                </div>
              </details>
            ) : (
              <p className="text-sm text-ink-3">問題なし</p>
            ))}
          <MarkdownPreview markdown={content.markdown} mermaid />
        </div>
      </div>
    </div>
  );
}
