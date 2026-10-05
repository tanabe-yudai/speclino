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
