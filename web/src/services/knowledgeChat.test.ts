import { describe, expect, it } from 'vitest'
import { ApiError } from '@/api/errors'
import {
  CHAT_NOTICE,
  applyProposal,
  applyRuleProposal,
  canSummarize,
  canonicalJson,
  chatsFor,
  currentFieldText,
  describeChatError,
  editNote,
  editorText,
  noteFromSummary,
  proposalText,
  ruleEditorText,
  idempotencyKeyFor,
  messageBytesLeft,
  textHash,
  utf8Bytes,
} from './knowledgeChat'
import type { ArtifactContent } from '@/types/knowledge'
import type { KnowledgeChat } from '@/types/knowledgeChat'
import type { PolicyRule } from '@/types/policy'

const scope = { workspace: true, projects: [] }
const skill: ArtifactContent = { kind: 'skill', skill_md: '---\nname: a\n---\n', files: { 'b.md': 'B', 'a.md': 'A' }, scope }
const instr: ArtifactContent = { kind: 'instructions', target: 'claude_md', body: '本文', scope }
const rule: PolicyRule = {
  id: 'R-1',
  version: '2',
  level: 'advisory',
  title: 't',
  text: 'x',
  applies_to: { paths: ['a'], languages: [] },
  verifier: null,
  on_missing_input: 'skipped',
  exceptions_allowed: false,
}

describe('knowledgeChat', () => {
  it('告知の文言は設計どおり', () => {
    expect(CHAT_NOTICE).toBe('会話は Google の Gemini に送られ、SpecLino の運用者が読む場合があります。ほかのメンバーには見えません')
  })

  it('編集欄の本文はキーの順に依らず、名前・スコープ・対象を含めない', () => {
    expect(editorText(instr)).toBe('本文')
    expect(editorText(skill)).toBe('{"files":{"a.md":"A","b.md":"B"},"skill_md":"---\\nname: a\\n---\\n"}')
    expect(canonicalJson({ b: 1, a: [{ d: 2, c: undefined }] })).toBe('{"a":[{"d":2}],"b":1}')
    expect(ruleEditorText(null)).toBe('{}')
  })

  it('ハッシュはサーバと同じ sha256（UTF-8）', async () => {
    // Python: hashlib.sha256("あ".encode()).hexdigest()
    expect(await textHash('あ')).toBe('sha256:dc5a4d3d82f7e15792959dc661538ae0e541ce66494516f5c9cfd9cd3308494d')
  })

  it('提案は許可した欄だけを編集欄に入れる', () => {
    expect(applyProposal(instr, { body: '新しい' })).toEqual({ ...instr, body: '新しい' })
    const next = applyProposal(skill, { skill_md: 'S' })
    expect(next).toEqual({ ...skill, skill_md: 'S' })
    expect(applyProposal(skill, { skill_md: 'S', files: {} })).toEqual({ ...skill, skill_md: 'S', files: {} })
    expect(applyProposal(instr, { markdown: 'x' })).toBe(instr)
  })

  it('規約の提案は版・対象が無いとき・例外の可否を残す', () => {
    const merged = applyRuleProposal(rule, {
      rule: { title: '新', level: 'mandatory', verifier: { id: 'lint.preset', version: '1.0.0', params: {} }, applies_to: { paths: ['b'] } },
    })
    expect(merged).toMatchObject({ id: 'R-1', version: '2', title: '新', level: 'mandatory', on_missing_input: 'skipped', exceptions_allowed: false })
    expect(merged.applies_to).toEqual({ paths: ['b'], languages: [] })
  })

  it('差分の前後', () => {
    expect(currentFieldText(skill)).toBe('=== SKILL.md\n---\nname: a\n---\n\n\n=== a.md\nA\n\n=== b.md\nB')
    expect(proposalText({ skill_md: 'S' }, skill)).toContain('=== a.md')
    expect(proposalText({ rule: { id: 'X' } })).toBe('{\n  "id": "X"\n}')
  })

  it('要約は本人が確かめる前提で説明の欄に入る', () => {
    const note = noteFromSummary({ what: 'a', why: 'b', dropped: 'c', note: 'x'.repeat(600) })
    expect(note.text).toHaveLength(500)
    expect(note.source).toBe('chat_summary')
    expect(editNote(note, '直した')).toEqual({ text: '直した', source: 'chat_summary' })
    expect(editNote(note, '  ')).toEqual({ text: '  ', source: 'manual' })
  })

  it('要約できるのは答えた発言があるときだけ', () => {
    const chat = (status: 'done' | 'failed'): KnowledgeChat => ({
      id: 'c',
      workspace: 'w',
      owner_uid: 'u',
      kind: 'spec',
      artifact_id: null,
      title: '',
      messages: [{ id: 'm', role: 'user', text: 'x', status, created_at: '' }],
      version: 1,
      created_at: '',
      updated_at: '',
    })
    expect(canSummarize(null)).toBe(false)
    expect(canSummarize(chat('failed'))).toBe(false)
    expect(canSummarize(chat('done'))).toBe(true)
  })

  it('一覧は編集中の知識の会話だけ', () => {
    const row = (id: string, artifact_id: string | null) => ({ id, kind: 'spec' as const, artifact_id, title: '', message_count: 0, version: 1, updated_at: '' })
    expect(chatsFor([row('1', null), row('2', 'a')], null).map((c) => c.id)).toEqual(['1'])
    expect(chatsFor([row('1', null), row('2', 'a')], 'a').map((c) => c.id)).toEqual(['2'])
  })

  it('エラーはサーバの固定の文を出す', () => {
    expect(describeChatError(new ApiError(429, '今日の発言の上限に達しました', 'message_limit'))).toBe('今日の発言の上限に達しました')
    expect(describeChatError(new ApiError(404, 'x', 'chat_not_found'))).toContain('見つかりません')
    expect(describeChatError(new Error('boom'))).toContain('通信に失敗')
  })

  it('発言の上限は UTF-8 のバイト数で数える', () => {
    expect(utf8Bytes('あa')).toBe(4)
    expect(messageBytesLeft('a'.repeat(8192))).toBe(0)
    expect(messageBytesLeft('あ'.repeat(2731))).toBe(-1)
    expect(describeChatError(new ApiError(413, 'x', 'message_too_long'))).toContain('8192 バイト')
  })

  it('失敗した発言の再送は同じ本文なら同じキー', () => {
    const retry = { text: '同じ', key: 'key-1' }
    expect(idempotencyKeyFor('同じ', retry)).toBe('key-1')
    expect(idempotencyKeyFor('違う', retry)).not.toBe('key-1')
    expect(idempotencyKeyFor('同じ', null)).not.toBe('key-1')
  })
})
