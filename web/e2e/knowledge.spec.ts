/**
 * チームの知識レジストリの通し確認（S72）。scripts/seed_demo.py で用意した DB に対して動かす。
 * E2E_SHOTS_DIR=../docs/screenshots でスクリーンショットを knowledge-*.png として保存する
 * （docs/demo-knowledge-registry.md）。
 */
import { expect, test, type Page } from '@playwright/test'
import path from 'node:path'

const SLUG = process.env.E2E_WORKSPACE ?? 'shop-team'
// スクリーンショットは E2E_SHOTS_DIR を設定したときだけ保存する（docs の画像を撮り直すとき）
const SHOTS = process.env.E2E_SHOTS_DIR ? path.resolve(process.env.E2E_SHOTS_DIR) : null
// seed_demo.py のプロジェクト（examples/shop/seed.py の REPO）
const PROJECT = '/p/example/shop'
const DEV_HEADERS = { 'X-Dev-User': 'judge' }

async function shot(page: Page, name: string) {
  if (!SHOTS) return
  await page.screenshot({ path: path.join(SHOTS, `knowledge-${name}.png`), fullPage: false })
}

async function login(page: Page) {
  await page.goto('/login')
  await page.getByRole('button', { name: '開発用ログイン' }).click()
  await page.waitForURL(/\/w\//)
}

test('仕様の編集・診断・承認、skill、可観測性、検査結果の確定', async ({ page, request }) => {
  await login(page)

  // 1. 上部バーの「知識」→ 種類を仕様に絞る → 仕様のエディタ
  await page.getByRole('navigation', { name: '主な画面' }).getByRole('link', { name: '知識', exact: true }).click()
  await page.getByRole('navigation', { name: '種類で絞る' }).getByRole('link', { name: '仕様', exact: true }).click()
  await page.waitForURL(/\/knowledge\?kind=spec/)
  await shot(page, 'list')
  await page.getByRole('link', { name: 'shop', exact: true }).click()
  await expect(page.getByRole('navigation', { name: 'パンくずリスト' }).getByRole('link', { name: '仕様' })).toBeVisible()
  const body = page.getByLabel('仕様の本文（Markdown と ```mermaid の図）')
  await expect(body).toBeVisible()
  const original = await body.inputValue()

  // 2. ガードに誤りを入れる → 行つきの診断が出る
  const broken = original.replace('before.inventory.available >= before.order.quantity', 'before.inventory.available >= before.order.qty')
  expect(broken).not.toBe(original)
  await body.fill(broken)
  await page.getByText(/診断の内容（[1-9]/).click() // 診断は折りたたみの中
  const diagnostic = page.getByRole('button', { name: /行 \d+ 列へ移る: .*before\.order\.qty/ })
  await expect(diagnostic).toBeVisible()
  await diagnostic.click() // 本文のその行へ移る
  await shot(page, 'spec-diagnostic')

  // 2b. `<` と `>=` を含むガードの辺ラベルも描ける（Mermaid のラベルの中の比較演算子。保存はしない）
  const withComparison = original.replace(
    '    notify -->|"[e4]"| done',
    '    notify -->|"[e4]"| done\n    check -->|"[e5] before.order.quantity < 1 and before.inventory.available >= 0"| done',
  )
  expect(withComparison).not.toBe(original)
  await body.fill(withComparison)
  await expect(page.getByText('問題なし', { exact: true })).toBeVisible()
  await expect(page.locator('svg[id^="speclino-mermaid"]')).toHaveCount(4)
  await expect(page.getByText(/図を描画できません/)).toHaveCount(0)
  await expect(page.locator('svg[id^="speclino-mermaid"]').last()).toContainText('before.order.quantity < 1 and before.inventory.available >= 0')

  // 3. 直す（説明の 1 文も足して保存できる差分にする）→ 診断が消え、図が描ける
  const fixed = original.replace('挟んでもよい（シーケンス図は「この順で含む」の義務）。', '挟んでもよい（シーケンス図は「この順で含む」の義務）。在庫が足りなければ何も呼ばない。')
  expect(fixed).not.toBe(original)
  await body.fill(fixed)
  await expect(page.getByText('問題なし', { exact: true })).toBeVisible()
  // 4 つの図（クラス・シーケンス・状態遷移・アクティビティ）がすべて描ける
  await expect(page.locator('svg[id^="speclino-mermaid"]')).toHaveCount(4)
  await expect(page.getByText(/図を描画できません/)).toHaveCount(0)
  await page.locator('svg[id^="speclino-mermaid"]').last().scrollIntoViewIfNeeded()
  await shot(page, 'spec-preview')

  // 4. 下書きを保存 → 承認 → 有効になる
  await page.getByRole('button', { name: '下書きを保存' }).click()
  await expect(page.getByText(/下書き 改訂 \d+ を保存しました/)).toBeVisible()
  await page.getByRole('button', { name: '承認して有効にする' }).click()
  await expect(page.getByRole('dialog')).toBeVisible()
  await page.waitForTimeout(500) // 開くアニメーションの後に撮る
  await shot(page, 'spec-approve')
  await page.getByRole('dialog').getByRole('button', { name: '承認して有効にする' }).click()
  await expect(page.getByText(/改訂 \d+ を承認しました/)).toBeVisible()

  // 5. skill のエディタ
  await page.goto(`/w/${SLUG}${PROJECT}/knowledge?kind=skill`)
  await page.getByRole('link', { name: 'shop-error-format', exact: true }).click()
  await expect(page.getByLabel('SKILL.md')).toHaveValue(/name: shop-error-format/)
  await shot(page, 'skill')

  // 6. 観測（旧いホームのタブ ?tab=usage は観測の画面へ転送される）
  await page.goto(`/w/${SLUG}?tab=usage`)
  await page.waitForURL(/\/observability$/)
  await expect(page.getByText(/集計 /)).toBeVisible()
  await shot(page, 'observability')
  await page.getByText('AI 判定の状況').evaluate((el) => el.scrollIntoView({ block: 'start' }))
  await page.mouse.wheel(0, -80)
  await expect(page.getByText('うち作成者との照合ができないもの')).toBeVisible()
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
  const hint = page.getByText(/対応表・免除・規約の変更の確定は、承認者が PR に \/speclino attest とコメントする/)
  await expect(hint).toBeVisible()
  await page.getByText('PR のコメントで確定するには').first().click()
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
  // 件数と理由は同じ集合から: 確定した指摘と、seed が main で確定した「指摘なし」は「確定済み・情報」、
  // 残る要確認は lock だけ（必須の規約の「指摘なし」も確定までは要確認。#114）
  await expect(page.getByText('要確認 1件 が残っているため失敗しました')).toBeVisible()
  await expect(page.getByText('確定済み・情報').first()).toBeVisible()
  await shot(page, 'run-recheck')
  await attested.evaluate((el) => el.scrollIntoView({ block: 'center' }))
  await shot(page, 'run-recheck-attested')
})
