/** `/api/workspaces/:slug/repositories` と `/api/github/callback` の応答（S55）。 */

export interface RegisteredRepository {
  repo_full_name: string
  github_repo_id: number | null
  default_branch: string
}

export interface AuthorizeResponse {
  authorize_url: string
}

export interface RegistrationResult {
  slug: string
  repo_full_name: string
  status: 'registered' | 'already_registered'
}
