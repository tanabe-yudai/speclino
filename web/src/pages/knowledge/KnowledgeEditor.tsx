import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react'
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { toast } from 'sonner'
import { ApiError } from '@/api/errors'
import { createArtifact, getDraft, listArtifactRevisions, saveDraft } from '@/api/knowledge'
import { ToneBadge } from '@/components/app/ToneBadge'
import { ErrorState } from '@/components/ErrorState'
import { ApprovalPanel } from '@/components/knowledge/ApprovalPanel'
import { ChatPanel } from '@/components/knowledge/ChatPanel'
import { ConflictDialog } from '@/components/knowledge/ConflictDialog'
import { InstructionsEditorPane } from '@/components/knowledge/InstructionsEditorPane'
import { KnowledgeNav } from '@/components/knowledge/KnowledgeNav'
import { NoteField } from '@/components/knowledge/NoteField'
import { NotFoundPanel } from '@/components/knowledge/NotFoundPanel'
import { ScopePicker } from '@/components/knowledge/ScopePicker'
import { SkillEditorPane } from '@/components/knowledge/SkillEditorPane'
import { SpecEditorPane } from '@/components/knowledge/SpecEditorPane'
import { LoadingState } from '@/components/LoadingState'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { useCurrentWorkspace } from '@/hooks/useCurrentWorkspace'
import { useKnowledgeChat } from '@/hooks/useKnowledgeChat'
import { useSpecPreview } from '@/hooks/useSpecPreview'
import { useWorkspaceProjects } from '@/hooks/useWorkspaceProjects'
import {
  KIND_LABELS,
  activeRevision,
  contentAfterSave,
  describeKnowledgeError,
  editBase,
  emptyContent,
  isSameContent,
  parseSection,
  revisionText,
  scopeProblem,
  toEditable,
  type EditBase,
} from '@/services/knowledge'
import { applyProposal, canSummarize, currentFieldText, editorText, proposalText } from '@/services/knowledgeChat'
import { paths } from '@/services/paths'
import { countErrors } from '@/services/specPreview'
import type { ArtifactContent, ArtifactKind, ArtifactRevision, SpecPreviewRequest } from '@/types/knowledge'
import type { NoteState, RawProposal } from '@/types/knowledgeChat'

const NAME_RE = /^[a-z0-9][a-z0-9-]*$/

/** 知識のエディタ（新規 / 既存）。仕様・スキル・指示書で本文の欄を切り替える。 */
export function KnowledgeEditor() {
  const { section, id } = useParams<{ section: string; id?: string }>()
  const workspace = useCurrentWorkspace()
  const kind = parseSection(section)
  if (!workspace) return <NotFoundPanel title="このワークスペースは見つかりません" />
  if (!kind) return <NotFoundPanel title="この種類の知識はありません" />
  return <EditorLoader key={`${workspace.slug}/${kind}/${id ?? ''}`} slug={workspace.slug} kind={kind} id={id ?? null} />
}

interface Loaded {
  draft: ArtifactRevision | null
  revisions: ArtifactRevision[]
}

function EditorLoader({ slug, kind, id }: { slug: string; kind: ArtifactKind; id: string | null }) {
  const [loaded, setLoaded] = useState<Loaded | null>(id ? null : { draft: null, revisions: [] })
  const [error, setError] = useState<string | null>(null)
  const [version, setVersion] = useState(0)

  useEffect(() => {
    if (!id) return
    let alive = true
    Promise.all([getDraft(slug, id), listArtifactRevisions(slug, id)])
      .then(([draft, revisions]) => alive && setLoaded({ draft, revisions }))
      .catch((err: unknown) => {
        if (!alive) return
        setError(err instanceof ApiError && err.status === 404 ? 'この知識は見つかりません' : err instanceof ApiError ? err.message : '知識を取得できませんでした')
      })
    return () => {
      alive = false
    }
  }, [slug, id, version])

  const reload = useCallback(() => {
    setError(null)
    setLoaded(null)
    setVersion((n) => n + 1)
  }, [])

  if (error) return <ErrorState message={error} onRetry={reload} />
  if (!loaded) return <LoadingState message="知識を取得しています…" />
  return <EditorForm key={version} slug={slug} kind={kind} id={id} initial={loaded} onReload={reload} />
}

function EditorForm({ slug, kind, id, initial, onReload }: { slug: string; kind: ArtifactKind; id: string | null; initial: Loaded; onReload: () => void }) {
  const navigate = useNavigate()
  const [searchParams] = useSearchParams()
  const chatParam = searchParams.get('chat')
  const { projects, error: projectsError } = useWorkspaceProjects(slug)
  const [base, setBase] = useState<EditBase>(() => editBase(initial.draft, initial.revisions))
  const [draft, setDraft] = useState<ArtifactRevision | null>(initial.draft)
  const [content, setContent] = useState<ArtifactContent>(() => (base.base ? toEditable(base.base.content) : emptyContent(kind)))
  const [name, setName] = useState('')
  const [paneInvalid, setPaneInvalid] = useState(false)
  const [saving, setSaving] = useState(false)
  const [saveError, setSaveError] = useState<string | null>(null)
  const [conflict, setConflict] = useState(false)
  const [resetKey, setResetKey] = useState(0)
  // 改訂の説明（S73）。下書きを直すときは今の下書きの説明から始める
  const [note, setNote] = useState<NoteState>(() => ({ text: initial.draft?.note ?? '', source: initial.draft?.note_source ?? 'manual' }))
  const [chatOpen, setChatOpen] = useState(chatParam !== null)
  const chat = useKnowledgeChat(slug, kind, id, chatParam && chatParam !== 'new' ? chatParam : null)
  // 保存の応答の時点の本文（送信後の入力を含む）を読むため
  const latest = useRef(content)
  useEffect(() => {
    latest.current = content
  }, [content])

  const active = activeRevision(initial.revisions)
  const artifactName = base.base?.name ?? null
  const baseContent = base.base ? toEditable(base.base.content) : emptyContent(kind)
  const dirty = !isSameContent(content, baseContent)
  const scopeIssue = scopeProblem(kind, content.scope)
  const nameIssue = id ? null : !name ? '名前を入力してください' : !NAME_RE.test(name) || name.length > 64 ? '名前は英小文字・数字・ハイフンで 64 文字以内（先頭は英小文字か数字）にします' : null

  const previewRequest: SpecPreviewRequest | null =
    content.kind === 'spec' && content.markdown.trim() && !paneInvalid
      ? { markdown: content.markdown, domain: content.domain, legacy_ir: content.legacy_ir }
      : null
  const { preview, error: previewError, pending: previewPending, last } = useSpecPreview(slug, previewRequest)
  const shownPreview = preview ?? last
  const previewErrors = countErrors(preview?.parse_result.diagnostics ?? [])

  const noteChanged = note.text.trim() !== (draft?.note ?? '')
  const saveBlock = nameIssue ?? scopeIssue ?? (paneInvalid ? '読めない欄があります（赤い欄を直してください）' : null) ?? (id && !dirty && !noteChanged ? '変更はありません' : null)

  function applyChatProposal(raw: RawProposal) {
    setContent(applyProposal(content, raw))
    // スキルの付属ファイル・仕様の JSON 欄を、反映した内容から作り直す
    setResetKey((k) => k + 1)
    toast.success('提案を編集欄に反映しました（まだ保存していません）')
  }

  function submit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault()
    if (saveBlock) return
    setSaving(true)
    setSaveError(null)
    const sent = content
    const request = id
      ? saveDraft(slug, id, base.expectedRevision as number, base.expectedHash as string, content, note)
      : createArtifact(slug, kind, name, content, note)
    request
      .then(async (rev) => {
        toast.success(`下書き 改訂 ${rev.revision} を保存しました`)
        if (!id) {
          // 会話で作り始めた知識: 最初の保存でできた知識に会話を結び付ける
          const bound = chat.current ? await chat.bind(rev.artifact_id) : null
          const target = paths.knowledgeItem(slug, kind, rev.artifact_id)
          navigate(bound ? `${target}?chat=${encodeURIComponent(bound.id)}` : target, { replace: true })
          return
        }
        setDraft(rev)
        setBase({ base: rev, expectedRevision: rev.revision, expectedHash: rev.content_hash })
        setNote({ text: rev.note, source: rev.note_source ?? 'manual' })
        // 保存中に入力した文字は消さない（そのときは dirty のまま残る）
        const next = contentAfterSave(sent, latest.current, rev)
        if (next.replaced) {
          setContent(next.content)
          setResetKey((k) => k + 1)
        }
      })
      .catch((err: unknown) => {
        const described = describeKnowledgeError(err)
        if (described.kind === 'conflict') setConflict(true)
        else setSaveError(described.message)
      })
      .finally(() => setSaving(false))
  }

  const label = KIND_LABELS[kind]
  return (
    <div className="space-y-6">
      <header className="min-w-0 space-y-3">
        <KnowledgeNav slug={slug} current={kind} />
        <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
          <h1 className="min-w-0 truncate text-2xl font-bold tracking-tight">
            {artifactName ? (
              <>
                {label} <span className="font-mono">{artifactName}</span>
              </>
            ) : (
              `${label}を作る`
            )}
          </h1>
          {active && <ToneBadge tone="ok">有効 改訂 {active.revision}</ToneBadge>}
          {draft && <ToneBadge tone="rev">下書き 改訂 {draft.revision}</ToneBadge>}
          {id && (
            <Link to={paths.knowledgeRevisions(slug, kind, id)} className="ml-auto text-sm text-primary underline-offset-4 hover:underline">
              改訂の履歴と差分
            </Link>
          )}
        </div>
        {id && !draft && active && <p className="text-sm text-ink-2">有効な改訂 {active.revision} を土台に編集します。保存すると次の改訂の下書きになります。</p>}
        <Button type="button" size="sm" variant={chatOpen ? 'secondary' : 'outline'} onClick={() => setChatOpen(!chatOpen)} aria-expanded={chatOpen}>
          {chatOpen ? 'AI と作るパネルを閉じる' : 'AI と作る'}
        </Button>
      </header>

      {chatOpen && (
        <ChatPanel
          chat={chat}
          editorText={editorText(content)}
          beforeText={() => currentFieldText(content)}
          afterText={(raw) => proposalText(raw, content)}
          onApply={applyChatProposal}
        />
      )}

      {id && (
        <ApprovalPanel
          slug={slug}
          draft={draft}
          dirty={dirty}
          previewErrors={previewErrors}
          previewPending={content.kind === 'spec' && (previewPending || (!preview && !previewError))}
          previewFailed={content.kind === 'spec' && !!previewError}
          onApproved={onReload}
          onConflict={() => setConflict(true)}
        />
      )}

      <form className="space-y-6" onSubmit={submit} noValidate>
        {!id && (
          <div className="grid max-w-md gap-1.5">
            <Label htmlFor="artifact-name">名前</Label>
            <Input
              id="artifact-name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="例: order-checkout"
              maxLength={64}
              aria-invalid={(!!name && !!nameIssue) || undefined}
              aria-describedby="artifact-name-help"
              required
            />
            <p id="artifact-name-help" className={name && nameIssue ? 'text-sm text-bad' : 'text-xs text-ink-3'}>
              {name && nameIssue ? nameIssue : '英小文字・数字・ハイフン。ワークスペースの中で種類ごとに一意です。'}
            </p>
          </div>
        )}
        <ScopePicker kind={kind} scope={content.scope} onChange={(scope) => setContent({ ...content, scope })} projects={projects} error={projectsError} />
        {content.kind === 'spec' && (
          <SpecEditorPane
            slug={slug}
            content={content}
            onChange={setContent}
            preview={shownPreview}
            previewError={previewError}
            previewPending={previewPending}
            saving={saving}
            onInvalidChange={setPaneInvalid}
            resetKey={resetKey}
          />
        )}
        {content.kind === 'skill' && <SkillEditorPane content={content} onChange={setContent} onInvalidChange={setPaneInvalid} resetKey={resetKey} />}
        {content.kind === 'instructions' && <InstructionsEditorPane content={content} onChange={setContent} />}

        <div className="max-w-3xl">
          <NoteField id="revision-note" note={note} onChange={setNote} summarize={canSummarize(chat.current) ? chat.summarize : undefined} />
        </div>

        <div className="sticky bottom-0 z-10 -mx-4 flex flex-wrap items-center gap-3 border-t bg-background/80 px-4 py-3 backdrop-blur-md sm:-mx-6 sm:px-6">
          <Button type="submit" disabled={!!saveBlock || saving} aria-describedby="save-status">
            {saving ? '保存しています…' : id ? '下書きを保存' : '下書きを作る'}
          </Button>
          <span id="save-status" className="text-sm text-ink-2">
            {saveBlock ?? (dirty ? '保存していない変更があります' : '')}
          </span>
          {saveError && (
            <p role="alert" className="w-full text-sm text-bad">
              {saveError}
            </p>
          )}
        </div>
      </form>

      <ConflictDialog open={conflict} ownText={dirty ? revisionText(content, new Map()) : ''} onReload={onReload} onClose={() => setConflict(false)} />
    </div>
  )
}
