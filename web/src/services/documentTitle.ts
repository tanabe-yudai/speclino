const APP_NAME = 'SpecLino'

/** タブのタイトル。「画面名 · SpecLino」、画面名が無ければ SpecLino だけ。 */
export function documentTitle(page: string | undefined): string {
  return page ? `${page} · ${APP_NAME}` : APP_NAME
}

/** 検査の画面のタイトル。見出しと同じく、owner を除いたリポジトリ名と PR 番号。 */
export function runPageTitle(repoFullName: string | null | undefined, prNumber: number | null | undefined): string {
  const repo = repoFullName?.split('/')[1] ?? '検査'
  return prNumber != null ? `${repo} #${prNumber}` : repo
}
