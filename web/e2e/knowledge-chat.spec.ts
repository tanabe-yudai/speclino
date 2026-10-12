/**
 * 対話による知識の作成の通し確認（S73）。会話の LLM は台本（`SPECLINO_CHAT_LLM=scripted`）。
 * ワークスペース（既定 `chat-team`、開発ログインの `judge` が owner）を先に作っておく
 * （`speclino workspace create chat-team --owner-uid judge`）。
 */
import { expect, test, type Page } from '@playwright/test'

const SLUG = process.env.E2E_CHAT_WORKSPACE ?? 'chat-team'
const NOTICE = '会話は Google の Gemini に送られ、SpecLino の運用者が読む場合があります。ほかのメンバーには見えません'

async function login(page: Page) {
  await page.goto('/login')
  await page.getByRole('button', { name: '開発用ログイン' }).click()
  await page.waitForURL(/\/w\//)
}

test('チャットで指示書を作り、要約を説明にして保存する', async ({ page, request }) => {
  await login(page)
  const name = `dev-rules-${Date.now()}`

  // 1. 知識の一覧を指示書に絞り「AI と指示書を作る」→ エディタで AI と作るパネルが最初から開き、告知が出る
  await page.goto(`/w/${SLUG}/knowledge?kind=instructions`)
  await page.getByRole('link', { name: 'AI と指示書を作る' }).click()
  await expect(page.getByText(NOTICE)).toBeVisible()

  // 2. 話しかける → 「本文を編集中…」が会話の返答の下に出て（差分の表は出さない）、編集欄は出たまま（S79）
  const input = page.getByLabel('AI への発言')
  await input.fill('開発の手順を AGENTS.md に書きたい')
  await expect(page.getByText(/残り \d+ バイト/)).toBeVisible()
  await page.getByRole('button', { name: '送る' }).click()
  const writing = page.getByRole('list', { name: '会話' }).getByRole('status').filter({ hasText: '本文を編集中…' })
  await expect(writing).toBeVisible()
  await expect(page.getByRole('list', { name: '会話' }).locator('table')).toHaveCount(0)
  await expect(writing.getByRole('button', { name: '止める' })).toBeVisible()
  // 送信中も入力欄は無効にしない（読み取り専用。スマホでキーボードを閉じない）
  await expect(input).toBeEditable({ editable: false })
  await expect(input).toBeEnabled()
  const body = page.getByLabel('本文', { exact: true })
  await expect(body).toBeVisible()
  await expect(page.getByText('下書きの案を作りました（台本の応答）')).toBeVisible()

  // 3. 書き終えると本文が編集欄に入り、反映前後の差分と「元に戻す」が出る（保存はまだ）。
  //    新規作成で名前の欄が空なので、名前の案も入る
  await expect(writing).toBeHidden()
  await expect(page.getByLabel('名前')).toHaveValue('scripted-draft')
  await expect(body).toHaveValue(/変更の前にテストを書く/)
  const applied = page.getByRole('region', { name: 'AI が反映した差分' })
  await expect(applied.getByRole('region', { name: '反映した差分' })).toContainText('変更の前にテストを書く')
  await expect(page.getByTestId('proposal-line')).toContainText('本文に反映しました')

  // 元に戻すと編集欄は元の本文に戻り、会話の「本文に反映」でもう一度入れられる
  await applied.getByRole('button', { name: '元に戻す' }).click()
  await expect(body).toHaveValue('')
  await page.getByTestId('proposal-line').getByRole('button', { name: /^(もう一度反映|本文に反映)$/ }).click()
  await expect(body).toHaveValue(/変更の前にテストを書く/)
  // 反映の後に編集すると、もう戻せない
  await body.fill(`${await body.inputValue()}\n追記`)
  await expect(page.getByText('反映後に編集したため戻せません')).toBeVisible()
  await expect(applied.getByRole('button', { name: '元に戻す' })).toBeDisabled()

  // 4. 会話の要約を説明に入れ、本人が直して保存する
  await page.getByLabel('名前').fill(name)
  await page.getByRole('button', { name: '会話の要約を使う' }).click()
  const note = page.getByLabel('変更の説明（任意）')
  await expect(note).toHaveValue(/何を: 下書きを AI と作った/)
  await note.fill(`${await note.inputValue()}\n（確認済み）`)
  await page.getByRole('button', { name: '下書きを作る' }).click()
  await page.waitForURL(/\/knowledge\/instructions\/[0-9a-f-]+\?chat=/)

  // 5. 保存した知識に会話が結び付き、続きから再開できる
  await expect(page.getByRole('navigation', { name: '自分の会話' })).toContainText('開発の手順を AGENTS.md に書きたい')
  await expect(page.getByText('下書きの案を作りました（台本の応答）')).toBeVisible()

  // 6. 改訂の画面に「変更の説明」と要約の印
  await page.getByRole('link', { name: '改訂の履歴と差分' }).click()
  const explained = page.getByLabel('変更の説明')
  await expect(explained).toContainText('何を: 下書きを AI と作った')
  await expect(explained).toContainText('（確認済み）')
  await expect(explained).toContainText('会話の要約から（作者が確認）')

  // 7. 他のメンバーの画面・API からは見えない（owner でも 404）
  const chats = await request.get(`/api/workspaces/${SLUG}/knowledge/chats?kind=instructions`, { headers: { 'X-Dev-User': 'judge' } })
  const [mine] = (await chats.json()) as { id: string }[]
  const other = await request.get(`/api/workspaces/${SLUG}/knowledge/chats/${mine.id}`, { headers: { 'X-Dev-User': 'someone-else' } })
  expect(other.status()).toBe(404)
})

test('チーム規約の画面で、提案をルールの編集欄に入れる', async ({ page }) => {
  await login(page)
  await page.goto(`/w/${SLUG}/knowledge/policies`)
  await page.getByRole('button', { name: 'AI と作る' }).click()
  await expect(page.getByText(NOTICE)).toBeVisible()
  await page.getByLabel('AI への発言').fill('README を置く規約を足したい')
  await page.getByRole('button', { name: '送る' }).click()
  await expect(page.getByText('診断のエラーなし')).toBeVisible()
  await page.getByRole('button', { name: 'エディタに反映' }).click()
  await expect(page.getByLabel('ID')).toHaveValue('DOC-001')
  await expect(page.getByLabel('題')).toHaveValue('README を置く')
  await expect(page.getByRole('button', { name: '会話の要約を使う' })).toBeVisible()
})
