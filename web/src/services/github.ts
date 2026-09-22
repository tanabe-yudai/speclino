/** GitHub（正本）への参照リンクを組み立てる。仕様は正本にリンクで参照するだけで、編集はしない。 */

export function githubPrUrl(repoFullName: string, prNumber: number): string {
  return `https://github.com/${repoFullName}/pull/${prNumber}`
}

export function githubChecksUrl(repoFullName: string, prNumber: number): string {
  return `https://github.com/${repoFullName}/pull/${prNumber}/checks`
}

export function githubBlobUrl(
  repoFullName: string,
  commitSha: string,
  path: string,
  lineStart?: number | null,
  lineEnd?: number | null,
): string {
  const url = `https://github.com/${repoFullName}/blob/${commitSha}/${path}`
  if (lineStart == null) return url
  const end = lineEnd != null && lineEnd !== lineStart ? `-L${lineEnd}` : ''
  return `${url}#L${lineStart}${end}`
}
