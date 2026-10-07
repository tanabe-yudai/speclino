export class ApiError extends Error {
  readonly status: number
  /** API-ERR-001 の `error.code`（取れたときだけ）。409 の理由の区別などに使う。 */
  readonly code: string | null

  constructor(status: number, message: string, code: string | null = null) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.code = code
  }
}

/** ストリームが `done` / `error` を受け取らずに終わった（途中で切れた）。 */
export class StreamInterruptedError extends Error {
  constructor() {
    super('応答が途中で切れました。もう一度送ってください')
    this.name = 'StreamInterruptedError'
  }
}
