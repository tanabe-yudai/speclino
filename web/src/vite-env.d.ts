/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** '1' でモック fixture を使う（実際の API 応答サンプル由来のデータを画面確認用に返す）。 */
  readonly VITE_MOCK?: string
  readonly VITE_FIREBASE_API_KEY?: string
  readonly VITE_FIREBASE_AUTH_DOMAIN?: string
  readonly VITE_FIREBASE_PROJECT_ID?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
