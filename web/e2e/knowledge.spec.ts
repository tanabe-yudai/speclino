/**
 * チームの知識レジストリの通し確認（S72）。scripts/seed_demo.py で用意した DB に対して動かす。
 * スクリーンショットは docs/screenshots/knowledge-*.png に保存する（docs/demo-knowledge-registry.md）。
 */
import { expect, test, type Page } from '@playwright/test'
import path from 'node:path'

const SLUG = process.env.E2E_WORKSPACE ?? 'shop-team'
const SHOTS = path.resolve(import.meta.dirname, '../../docs/screenshots')
const DEV_HEADERS = { 'X-Dev-User': 'judge' }

async function shot(page: Page, name: string) {
  await page.screenshot({ path: path.join(SHOTS, `knowledge-${name}.png`), fullPage: false })
}

async function login(page: Page) {
  await page.goto('/login')
  await page.getByRole('button', { name: '開発モードで入る' }).click()
  await page.waitForURL(/\/w\//)
}

async function currentGeneration(page: Page): Promise<number> {
  await page.goto(`/w/${SLUG}/knowledge/specs`)
  const text = await page.getByText(/今の bundle: 世代 \d+/).innerText()
  return Number(/世代 (\d+)/.exec(text)?.[1])
}

test('仕様の編集・診断・承認、skill、可観測性、検査結果の確定', async ({ page, request }) => {
  await login(page)

  // 1. 仕様の一覧 → 仕様のエディタ
  const before = await currentGeneration(page)
  await shot(page, 'list')
  await page.getByRole('link', { name: 'shop', exact: true }).click()
  const body = page.getByLabel('仕様の本文（Markdown と ```mermaid の図）')
  await expect(body).toBeVisible()
  const original = await body.inputValue()

  // 2. ガードに誤りを入れる → 行つきの診断が出る
  const broken = original.replace('before.inventory.available >= before.order.quantity', 'before.inventory.available >= before.order.qty')
  expect(broken).not.toBe(original)
  await body.fill(broken)
  const diagnostic = page.getByRole('button', { name: /行 \d+ 列へ移る: .*before\.order\.qty/ })
  await expect(diagnostic).toBeVisible()
  await expect(page.getByRole('tab', { name: /診断（[1-9]/ })).toBeVisible()
  await diagnostic.click() // 本文のその行へ移る
  await shot(page, 'spec-diagnostic')

  // 3. 直す（説明の 1 文も足して保存できる差分にする）→ 診断が消え、図が描ける
  const fixed = original.replace('挟んでもよい（シーケンス図は「この順で含む」の義務）。', '挟んでもよい（シーケンス図は「この順で含む」の義務）。在庫が足りなければ何も呼ばない。')
  expect(fixed).not.toBe(original)
  await body.fill(fixed)
  await expect(page.getByText('診断はありません。')).toBeVisible()
  await page.getByRole('tab', { name: '図' }).click()
  // 4 つの図（クラス・シーケンス・状態遷移・アクティビティ）がすべて描ける
  await expect(page.locator('figure svg')).toHaveCount(4)
  await expect(page.getByText('描画できない')).toHaveCount(0)
  await page.locator('figure').last().scrollIntoViewIfNeeded()
  await shot(page, 'spec-preview')

  // 4. 下書きを保存 → 承認 → bundle の世代が進む
  await page.getByRole('button', { name: '下書きを保存' }).click()
  await expect(page.getByText(/下書き 改訂 \d+ を保存しました/)).toBeVisible()
  await page.getByRole('button', { name: '承認して有効にする' }).click()
  await expect(page.getByRole('dialog')).toBeVisible()
  await page.waitForTimeout(500) // 開くアニメーションの後に撮る
  await shot(page, 'spec-approve')
  await page.getByRole('dialog').getByRole('button', { name: '承認して有効にする' }).click()
  await expect(page.getByText(new RegExp(`bundle の世代 ${before + 1}`))).toBeVisible()
  expect(await currentGeneration(page)).toBe(before + 1)

  // 5. skill のエディタ
  await page.goto(`/w/${SLUG}/knowledge/skills`)
  await page.getByRole('link', { name: 'shop-error-format', exact: true }).click()
  await expect(page.getByLabel('SKILL.md')).toHaveValue(/name: shop-error-format/)
  await shot(page, 'skill')

  // 6. 可観測性
  await page.goto(`/w/${SLUG}/observability`)
  await expect(page.getByText(/今の bundle: 世代 \d+/)).toBeVisible()
  await shot(page, 'observability')
  await page.getByText('LLM 支援の安定度').evaluate((el) => el.scrollIntoView({ block: 'start' }))
  await page.mouse.wheel(0, -80)
  await expect(page.getByText('うち自己確定かどうか照合できない')).toBeVisible()
  await shot(page, 'observability-llm')

  // 7. 検査結果: LLM 支援の REVIEW（確定のボタン）と、bindings の変更（PR コメントの案内）
  const runs = (await (await request.get(`/api/review-runs?limit=50&workspace=${SLUG}`, { headers: DEV_HEADERS })).json()) as {
    id: string
    pr_number: number | null
  }[]
  const runOf = (pr: number) => {
    const run = runs.find((r) => r.pr_number === pr)
    if (!run) throw new Error(`PR #${pr} の run が無い`)
    return run.id
  }
  await page.goto(`/runs/${runOf(7)}`)
  await expect(page.getByText(/SHOP-ERR-001: 「.*」に該当する/).first()).toBeVisible()
  const attest = page.getByRole('button', { name: '問題なしとして確定' }).first()
  await expect(attest).toBeVisible()
  await attest.scrollIntoViewIfNeeded()
  await shot(page, 'run-llm-review')

  await page.goto(`/runs/${runOf(6)}`)
  const hint = page.getByText(/対応表・免除・規約の変更の確定は PR コメントで行います/)
  await expect(hint).toBeVisible()
  await expect(page.locator('code', { hasText: '/speclino attest KNOWLEDGE-BINDINGS-001' })).toBeVisible()
  await hint.scrollIntoViewIfNeeded()
  await shot(page, 'run-bindings-review')

  await page.goto(`/runs/${runOf(1)}`)
  const violation = page.getByText(/Payment\.charge の呼出しを期待したが/).first()
  await expect(violation).toBeVisible()
  await violation.evaluate((el) => el.scrollIntoView({ block: 'start' }))
  await page.mouse.wheel(0, -120)
  await shot(page, 'run-violated')

  // 8. 確定する → 同じ PR の再検査へ移る
  await page.goto(`/runs/${runOf(7)}`)
  await page.getByRole('button', { name: '問題なしとして確定' }).first().click()
  await page.waitForURL((url) => !url.pathname.endsWith(runOf(7)))
  // 再検査（trigger=attestation）の完了を待つ。確定した指摘はゲートを止めない
  await expect
    .poll(
      async () => {
        await page.reload()
        return page.getByText('実行がまだ完了していないため').count()
      },
      { timeout: 60_000, intervals: [1_000] },
    )
    .toBe(0)
  const attested = page.getByText(/問題なしとして確定済み/).first()
  await expect(attested).toBeVisible()
  await shot(page, 'run-recheck')
  await attested.evaluate((el) => el.scrollIntoView({ block: 'center' }))
  await shot(page, 'run-recheck-attested')
})
