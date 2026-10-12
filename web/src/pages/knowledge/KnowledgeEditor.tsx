import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react'
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { toast } from 'sonner'
import { ApiError } from '@/api/errors'
import { createArtifact, getDraft, listArtifactRevisions, saveDraft } from '@/api/knowledge'
import { ToneBadge } from '@/components/app/ToneBadge'
import { ErrorState } from '@/components/ErrorState'
import { ApprovalPanel } from '@/components/knowledge/ApprovalPanel'
import { AppliedDiffPanel } from '@/components/knowledge/AppliedDiffPanel'
import { ChatPanel } from '@/components/knowledge/ChatPanel'
import { ConflictDialog } from '@/components/knowledge/ConflictDialog'
import { InstructionsEditorPane } from '@/components/knowledge/InstructionsEditorPane'
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
  chatOpenAtStart,
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
import { findFromProject, scopeForProject } from '@/services/knowledgeNav'
import { knowledgeCrumbs } from '@/services/breadcrumbs'
import { Breadcrumbs } from '@/components/shell/Breadcrumbs'
import { bodyText, canUndo, decideAutoApply, restoreApplied, touchesFiles } from '@/services/chatDraft'
import { applyProposal, canSummarize, currentFieldText, editorText, proposalText, proposedName, textHash } from '@/services/knowledgeChat'
import { paths } from '@/services/paths'
import { countErrors } from '@/services/specPreview'
import type { ArtifactContent, ArtifactKind, ArtifactRevision, SpecPreviewRequest } from '@/types/knowledge'
import type { AppliedEdit, ProposalOutcome } from '@/types/chatDraft'
import type { ChatTurn, NoteState, RawProposal } from '@/types/knowledgeChat'
import { useDocumentTitle } from '@/hooks/useDocumentTitle'

const NAME_RE = /^[a-z0-9][a-z0-9-]*$/

/** 知識のエディタ（新規 / 既存）。仕様・スキル・指示書で本文の欄を切り替える。 */
export function KnowledgeEditor() {
  const { section, id } = useParams<{ section: string; id?: string }>()
  const workspace = useCurrentWorkspace()
  const kind = parseSection(section)
  useDocumentTitle(kind ? (id ? `${KIND_LABELS[kind]}の編集` : `新しい${KIND_LABELS[kind]}`) : '編集')
  if (!workspace) return <NotFoundPanel title="このワークスペースは見つかりません" />
  if (!kind) return <NotFoundPanel title="この種類の項目はありません" />
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
        setError(err instanceof ApiError && err.status === 404 ? 'この内容は見つかりません' : err instanceof ApiError ? err.message : '内容を取得できませんでした')
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
  if (!loaded) return <LoadingState message="内容を取得しています…" />
  return <EditorForm key={version} slug={slug} kind={kind} id={id} initial={loaded} onReload={reload} />
}

function EditorForm({ slug, kind, id, initial, onReload }: { slug: string; kind: ArtifactKind; id: string | null; initial: Loaded; onReload: () => void }) {
  const navigate = useNavigate()
  const [searchParams] = useSearchParams()
  const chatParam = searchParams.get('chat')
  // S74: プロジェクトのタブから開いたときの戻り先（owner/repo）。そのプロジェクトのスコープで作り始める
  const from = searchParams.get('from')
  const { projects, error: projectsError } = useWorkspaceProjects(slug)
  const fromProject = findFromProject(projects, from)
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
  const role = useCurrentWorkspace()?.role ?? null
  const [chatOpen, setChatOpen] = useState(() => chatOpenAtStart(initial.draft, role))
  // AI が本文に反映した記録（元に戻す・差分の表示）と、提案ごとの扱い（S76）
  const [applied, setApplied] = useState<AppliedEdit | null>(null)
  const [outcomes, setOutcomes] = useState<Record<string, ProposalOutcome>>({})
  const chat = useKnowledgeChat(slug, kind, id, chatParam && chatParam !== 'new' ? chatParam : null)
  // 保存の応答の時点の本文（送信後の入力を含む）を読むため
  const latest = useRef(content)
  useEffect(() => {
    latest.current = content
  }, [content])
  // 新規で from のプロジェクトが分かったら、スコープをそのプロジェクトにする（1 回だけ。選び直しは尊重する）
  const seededScope = useRef(false)
  const fromProjectId = fromProject?.id
  useEffect(() => {
    if (id || !fromProjectId || seededScope.current) return
    seededScope.current = true
    setContent((c) => ({ ...c, scope: scopeForProject(fromProjectId) }))
  }, [id, fromProjectId])

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

  /** 提案を編集欄に入れる。反映前の本文を覚え、差分の表示と「元に戻す」に使う。 */
  function applyToEditor(raw: RawProposal, messageId: string | null): boolean {
    const cur = latest.current
    const next = applyProposal(cur, raw)
    if (next === cur) return false
    setContent(next)
    // スキルの付属ファイル・仕様の JSON 欄を、反映した内容から作り直す
    setResetKey((k) => k + 1)
    setApplied({
      messageId,
      before: bodyText(cur),
      after: bodyText(next),
      afterEditorText: editorText(next),
      ...(cur.kind === 'skill' && next.kind === 'skill' ? { beforeFiles: { ...cur.files }, afterFiles: { ...next.files } } : {}),
    })
    return true
  }

  function applyChatProposal(raw: RawProposal) {
    suggestName(raw)
    if (applyToEditor(raw, null)) toast.success('提案を編集欄に反映しました（まだ保存していません）')
  }

  /** 新規作成で名前の欄が空のときだけ、AI の名前の案を入れる（人が直せる。保存時の検査はサーバ）。 */
  function suggestName(raw: RawProposal) {
    setName((cur) => proposedName(cur, raw, !id))
  }

  /**
   * 確定した返答の提案を、条件が揃うときだけ本文に入れる。冪等の再送では提案が別の本文に向けた
   * ものでありうるので、送った本文のハッシュが提案の土台と一致することも確かめる。
   * 返す Promise が終わるまで、会話は生成中の表示を閉じない（S79）。
   */
  function onChatReply(turn: ChatTurn, sentEditorText: string): Promise<void> | undefined {
    const reply = turn.reply
    const proposal = reply?.proposal
    if (!reply || !proposal) return
    suggestName(proposal.raw_proposal)
    return textHash(sentEditorText).then((sentHash) => {
      const cur = latest.current
      const decision = decideAutoApply({
        proposal,
        sentEditorText,
        currentEditorText: editorText(cur),
        touchesFiles: touchesFiles(cur, proposal.raw_proposal),
        baseMatches: sentHash === proposal.base_hash,
      })
      if (decision.action === 'skip') {
        setOutcomes((o) => ({ ...o, [reply.id]: { status: 'skipped', reason: decision.reason } }))
      } else if (applyToEditor(proposal.raw_proposal, reply.id)) {
        setOutcomes((o) => ({ ...o, [reply.id]: { status: 'applied' } }))
      }
    })
  }

  /** 反映の取り消し。編集欄が反映した直後のままのときだけ（その後の編集を消さない）。 */
  function undoApplied() {
    const cur = latest.current
    if (!applied || !canUndo(applied, editorText(cur), cur)) return
    setContent(restoreApplied(cur, applied))
    setResetKey((k) => k + 1)
    setApplied(null)
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
          navigate(paths.knowledgeItem(slug, kind, rev.artifact_id, { chat: bound?.id, from }), { replace: true })
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
        <Breadcrumbs items={knowledgeCrumbs({ slug, kind, name: artifactName, fromProject })} />
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
            <Link to={paths.knowledgeRevisions(slug, kind, id, from)} className="ml-auto text-sm text-primary underline-offset-4 hover:underline">
              改訂の履歴と差分
            </Link>
          )}
        </div>
        {id && !draft && active && <p className="text-sm text-ink-2">保存すると新しい下書きになります。承認されるまで今の内容が使われます。</p>}
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
          inline
          bodyText={bodyText(content)}
          outcomes={outcomes}
          onReply={onChatReply}
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
        {applied && (
          <AppliedDiffPanel applied={applied} undoable={canUndo(applied, editorText(content), content)} onUndo={undoApplied} onClose={() => setApplied(null)} />
        )}
        {/* AI が書いている間も本文の欄は出したまま（途中経過は会話の中。ページの高さを変えない） */}
        {content.kind === 'spec' && (
          <SpecEditorPane
            content={content}
            onChange={setContent}
            preview={shownPreview}
            previewError={previewError}
            previewPending={previewPending}
            onInvalidChange={setPaneInvalid}
            resetKey={resetKey}
          />
        )}
        {content.kind === 'skill' && <SkillEditorPane content={content} onChange={setContent} onInvalidChange={setPaneInvalid} resetKey={resetKey} />}
        {content.kind === 'instructions' && <InstructionsEditorPane content={content} onChange={setContent} />}

        <div className="max-w-3xl">
          <NoteField id="revision-note" note={note} onChange={setNote} summarize={canSummarize(chat.current) ? chat.summarize : undefined} />
        </div>

        {/* スマホ幅は画面下のタブ（h-14 ＋安全領域）の上に置く */}
        <div className="sticky bottom-[calc(3.5rem+env(safe-area-inset-bottom))] z-10 -mx-4 sm:bottom-0 flex flex-wrap items-center gap-3 border-t bg-background/80 px-4 py-3 backdrop-blur-md sm:-mx-6 sm:px-6">
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
