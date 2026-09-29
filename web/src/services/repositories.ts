/** リポジトリ登録の画面で使う判定（S55）。 */

const REPO_FULL_NAME = /^[A-Za-z0-9-]+\/[A-Za-z0-9._-]+$/

/** `owner/name` の形か。サーバーでも同じ規則で検証する。 */
export function isRepoFullName(value: string): boolean {
  return REPO_FULL_NAME.test(value)
}

export function githubRepoUrl(fullName: string): string {
  return `https://github.com/${fullName}`
}
