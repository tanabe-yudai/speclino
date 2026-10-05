import { describe, expect, it } from 'vitest'
import { ApiError } from '@/api/errors'
import {
  activeRevision,
  approvalState,
  describeKnowledgeError,
  editBase,
  emptyContent,
  isSameContent,
  parseSection,
  scopeProblem,
} from './knowledge'
import type { ArtifactRevision, ArtifactScope } from '@/types/knowledge'

const rev = (over: Partial<ArtifactRevision>): ArtifactRevision => ({
  artifact_id: 'a1',
  workspace: 'w',
  kind: 'instructions',
  name: 'agents',
  revision: 1,
  status: 'active',
  content: { kind: 'instructions', target: 'agents_md', body: 'x', scope: { workspace: true, projects: [] } },
  content_hash: 'sha256:1',
  author: 'u1',
  approver: null,
  self_approved: false,
  note: '',
  created_at: '2026-10-05T00:00:00Z',
  approved_at: null,
  ...over,
})

describe('parseSection', () => {
  it('maps url sections to kinds', () => {
    expect(parseSection('specs')).toBe('spec')
    expect(parseSection('skills')).toBe('skill')
    expect(parseSection('instructions')).toBe('instructions')
    expect(parseSection('policies')).toBeNull()
    expect(parseSection(undefined)).toBeNull()
  })
})

describe('emptyContent', () => {
  it('gives each kind a starting content; spec starts with project scope', () => {
    expect(emptyContent('spec')).toMatchObject({ kind: 'spec', scope: { workspace: false, projects: [] }, compiled: null })
    expect(emptyContent('skill')).toMatchObject({ kind: 'skill', files: {}, scope: { workspace: true, projects: [] } })
    expect(emptyContent('instructions')).toMatchObject({ kind: 'instructions', target: 'agents_md' })
  })
})

describe('editBase', () => {
  it('edits the draft when there is one, else the active revision', () => {
    const active = rev({ revision: 1, status: 'active', content_hash: 'sha256:a' })
    const draft = rev({ revision: 2, status: 'draft', content_hash: 'sha256:d' })
    expect(editBase(draft, [draft, active])).toEqual({ base: draft, expectedRevision: 2, expectedHash: 'sha256:d' })
    expect(editBase(null, [active])).toEqual({ base: active, expectedRevision: 1, expectedHash: 'sha256:a' })
    expect(editBase(null, [])).toEqual({ base: null, expectedRevision: null, expectedHash: null })
  })

  it('finds the active revision among superseded ones', () => {
    const list = [rev({ revision: 3, status: 'active' }), rev({ revision: 2, status: 'superseded' })]
    expect(activeRevision(list)?.revision).toBe(3)
  })
})

describe('scopeProblem', () => {
  const s = (over: Partial<ArtifactScope>): ArtifactScope => ({ workspace: false, projects: [], ...over })
  it('requires projects for specs and one form for the others', () => {
    expect(scopeProblem('spec', s({ workspace: true }))).toBe('仕様のスコープはプロジェクトを 1 つ以上選びます')
    expect(scopeProblem('spec', s({ projects: ['p'] }))).toBeNull()
    expect(scopeProblem('skill', s({}))).toBe('ワークスペース全体か、プロジェクトを 1 つ以上選びます')
    expect(scopeProblem('skill', s({ workspace: true }))).toBeNull()
  })
})

describe('isSameContent', () => {
  it('compares content structurally', () => {
    const a = rev({}).content
    expect(isSameContent(a, { ...a })).toBe(true)
    expect(isSameContent(a, { ...a, body: 'y' } as typeof a)).toBe(false)
  })
})

describe('approvalState', () => {
  const draft = rev({ status: 'draft', revision: 2, author: 'u1' })
  const base = { uid: 'u2', draft, dirty: false, previewErrors: 0, previewPending: false }

  it('hides the button from members', () => {
    expect(approvalState({ ...base, role: 'member' }).visible).toBe(false)
  })

  it('requires a saved draft, no unsaved changes and (for specs) no errors', () => {
    expect(approvalState({ ...base, role: 'approver', draft: null })).toMatchObject({ visible: true, enabled: false, reason: '承認する下書きがありません' })
    expect(approvalState({ ...base, role: 'owner', dirty: true }).reason).toBe('保存していない変更があります。保存してから承認します')
    const spec = rev({ ...draft, kind: 'spec' })
    expect(approvalState({ ...base, role: 'owner', draft: spec, previewErrors: 2 }).reason).toBe('診断にエラーが 2 件あります')
    expect(approvalState({ ...base, role: 'owner', draft: spec, previewPending: true }).reason).toBe('保存した本文の診断を確かめています')
    expect(approvalState({ ...base, role: 'owner' })).toEqual({ visible: true, enabled: true, reason: null, selfApproval: false })
  })

  it('warns about self approval', () => {
    expect(approvalState({ ...base, role: 'owner', uid: 'u1' }).selfApproval).toBe(true)
  })
})

describe('describeKnowledgeError', () => {
  it('distinguishes the 409 reasons and the other API errors', () => {
    expect(describeKnowledgeError(new ApiError(409, 'm', 'draft_changed')).kind).toBe('conflict')
    expect(describeKnowledgeError(new ApiError(409, '同名の skill', 'bundle_conflict'))).toEqual({
      kind: 'bundle_conflict',
      message: '他の有効な知識と競合するため承認できません: 同名の skill',
    })
    expect(describeKnowledgeError(new ApiError(422, 'x', 'spec_not_compilable')).kind).toBe('not_compilable')
    expect(describeKnowledgeError(new ApiError(403, 'x', 'not_an_approver')).message).toBe('承認できるのは owner / approver だけです')
    expect(describeKnowledgeError(new ApiError(422, '名前が不正', 'invalid_artifact'))).toEqual({ kind: 'invalid', message: '名前が不正' })
    expect(describeKnowledgeError(new ApiError(409, 'x', 'artifact_exists')).message).toBe('同じ名前の知識が既にあります')
    expect(describeKnowledgeError(new Error('boom')).kind).toBe('other')
  })
})
