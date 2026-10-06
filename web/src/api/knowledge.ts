/** 知識レジストリ（S62）と仕様のプレビュー（S66）のエンドポイント関数。判断は持たない。 */

import { apiFetch } from './client'
import { ApiError } from './errors'
import type {
  ApproveResult,
  ArtifactContent,
  ArtifactKind,
  ArtifactRevision,
  ArtifactSummary,
  Bundle,
  SpecPreview,
  SpecPreviewRequest,
} from '../types/knowledge'

const ws = (slug: string) => `/api/workspaces/${encodeURIComponent(slug)}`
const art = (slug: string, id: string) => `${ws(slug)}/artifacts/${encodeURIComponent(id)}`
const json = { 'Content-Type': 'application/json' }

/** 404 のうち、指定した code のときだけ null にする（ワークスペースが無い 404 は投げる）。 */
async function nullOn404<T>(promise: Promise<T>, code: string): Promise<T | null> {
  try {
    return await promise
  } catch (err) {
    if (err instanceof ApiError && err.status === 404 && err.code === code) return null
    throw err
  }
}

export function listArtifacts(slug: string): Promise<ArtifactSummary[]> {
  return apiFetch<ArtifactSummary[]>(`${ws(slug)}/artifacts`)
}

export function createArtifact(
  slug: string,
  kind: ArtifactKind,
  name: string,
  content: ArtifactContent,
): Promise<ArtifactRevision> {
  return apiFetch<ArtifactRevision>(`${ws(slug)}/artifacts`, {
    method: 'POST',
    headers: json,
    body: JSON.stringify({ kind, name, content }),
  })
}

/** 下書きが無ければ null。 */
export function getDraft(slug: string, id: string): Promise<ArtifactRevision | null> {
  return nullOn404(apiFetch<ArtifactRevision>(`${art(slug, id)}/draft`), 'draft_not_found')
}

/** expected は保存の前提にした改訂（下書きがあればそれ、無ければ有効な改訂）。違えば 409 draft_changed。 */
export function saveDraft(
  slug: string,
  id: string,
  expectedRevision: number,
  expectedHash: string,
  content: ArtifactContent,
): Promise<ArtifactRevision> {
  return apiFetch<ArtifactRevision>(`${art(slug, id)}/draft`, {
    method: 'PUT',
    headers: json,
    body: JSON.stringify({ expected_revision: expectedRevision, expected_hash: expectedHash, content }),
  })
}

export function approveArtifact(
  slug: string,
  id: string,
  expectedRevision: number,
  expectedHash: string,
): Promise<ApproveResult> {
  return apiFetch<ApproveResult>(`${art(slug, id)}/approve`, {
    method: 'POST',
    headers: json,
    body: JSON.stringify({ expected_revision: expectedRevision, expected_hash: expectedHash }),
  })
}

/** 新しい順。 */
export function listArtifactRevisions(slug: string, id: string): Promise<ArtifactRevision[]> {
  return apiFetch<ArtifactRevision[]>(`${art(slug, id)}/revisions`)
}

/** まだ bundle が無ければ null。 */
export function getCurrentBundle(slug: string): Promise<Bundle | null> {
  return nullOn404(apiFetch<Bundle>(`${ws(slug)}/bundles/current`), 'bundle_not_found')
}

export function previewSpec(slug: string, body: SpecPreviewRequest, signal?: AbortSignal): Promise<SpecPreview> {
  return apiFetch<SpecPreview>(`${ws(slug)}/specs/preview`, {
    method: 'POST',
    headers: json,
    body: JSON.stringify(body),
    signal,
  })
}
