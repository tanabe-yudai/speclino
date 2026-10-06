import { defineConfig, devices } from '@playwright/test'

/**
 * ブラウザの通し確認（S72）。サーバー（web/dist を配る FastAPI）は先に起動しておく。
 * 手順と環境変数は docs/demo-knowledge-registry.md。
 */
export default defineConfig({
  testDir: './e2e',
  timeout: 120_000,
  expect: { timeout: 15_000 },
  workers: 1,
  reporter: 'list',
  use: {
    baseURL: process.env.E2E_BASE_URL ?? 'http://localhost:8080',
    ...devices['Desktop Chrome'],
    viewport: { width: 1280, height: 900 },
    locale: 'ja-JP',
  },
})
