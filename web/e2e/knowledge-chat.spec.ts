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

  // 1. チーム共通の指示書の「AI と作る」→ エディタで AI と作るパネルが開き、告知が出る
  await page.goto(`/w/${SLUG}?tab=shared`)
  await page.getByRole('region', { name: '指示書' }).getByRole('link', { name: 'AI と作る' }).click()
  await expect(page.getByText(NOTICE)).toBeVisible()

  // 2. 話しかける → 説明と提案（差分）が返る
  await page.getByLabel('AI への発言').fill('開発の手順を AGENTS.md に書きたい')
  await expect(page.getByText(/残り \d+ バイト/)).toBeVisible()
  await page.getByRole('button', { name: '送る' }).click()
  await expect(page.getByText('下書きの案を作りました（台本の応答）')).toBeVisible()
  await expect(page.getByRole('region', { name: '提案の差分' })).toContainText('変更の前にテストを書く')

  // 3. 「エディタに反映」は編集欄だけを変える
  const body = page.getByLabel('本文')
  await page.getByRole('button', { name: 'エディタに反映' }).click()
  await expect(body).toHaveValue(/変更の前にテストを書く/)
  // 反映の後は編集欄が変わったので、同じ提案は古くなり反映できない
  await expect(page.getByRole('button', { name: 'エディタに反映' })).toBeDisabled()

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
  await page.goto(`/w/${SLUG}/policies`)
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
