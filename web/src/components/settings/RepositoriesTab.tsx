import { ExternalLink } from "lucide-react";
import { useCallback, useEffect, useState, type FormEvent } from "react";
import { listRepositories, startRegistration } from "@/api/repositories";
import { ApiError } from "@/api/errors";
import { Panel, PanelTitle } from "@/components/app/Panel";
import { ErrorState } from "@/components/ErrorState";
import { LoadingState } from "@/components/LoadingState";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  GITHUB_APP_INSTALL_URL,
  githubRepoUrl,
  isRepoFullName,
} from "@/services/repositories";
import type { RegisteredRepository } from "@/types/repository";

/** ワークスペースに登録されたリポジトリの一覧と、owner による追加（S55）。 */
export function RepositoriesTab({
  slug,
  isOwner,
}: {
  slug: string;
  isOwner: boolean;
}) {
  const [repos, setRepos] = useState<RegisteredRepository[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [repoName, setRepoName] = useState("");
  const [starting, setStarting] = useState(false);
  const [startError, setStartError] = useState<string | null>(null);

  const fetchRepos = useCallback(() => {
    listRepositories(slug)
      .then(setRepos)
      .catch((err: unknown) =>
        setError(
          err instanceof ApiError
            ? err.message
            : "リポジトリ一覧の取得に失敗しました",
        ),
      );
  }, [slug]);

  useEffect(() => {
    fetchRepos();
  }, [fetchRepos]);

  function retry() {
    setError(null);
    setRepos(null);
    fetchRepos();
  }

  function handleAdd(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const name = repoName.trim();
    if (!isRepoFullName(name)) {
      setStartError(
        "owner/name の形で入力してください（例: your-org/your-repo）",
      );
      return;
    }
    setStarting(true);
    setStartError(null);
    startRegistration(slug, name)
      .then(({ authorize_url }) => window.location.assign(authorize_url))
      .catch((err: unknown) => {
        setStartError(
          err instanceof ApiError ? err.message : "登録を始められませんでした",
        );
        setStarting(false);
      });
  }

  return (
    <div className="space-y-6">
      <p className="text-sm text-ink-2">
        登録したリポジトリの PR
        だけが、このワークスペースのメンバーに表示されます。
      </p>
      {repos === null && error === null && (
        <LoadingState message="リポジトリを取得しています…" />
      )}
      {error && <ErrorState message={error} onRetry={retry} />}
      {repos !== null && repos.length === 0 && (
        <p className="text-sm text-ink-3">まだ登録されていません。</p>
      )}
      {repos !== null && repos.length > 0 && (
        <ul className="space-y-2">
          {repos.map((r) => (
            <li
              key={r.repo_full_name}
              className="row flex items-center gap-3 px-4 py-3 text-sm"
            >
              <a
                href={githubRepoUrl(r.repo_full_name)}
                target="_blank"
                rel="noreferrer"
                className="min-w-0 flex-1 truncate font-mono text-xs hover:underline"
              >
                {r.repo_full_name}
              </a>
              <span className="shrink-0 text-xs text-ink-3">
                既定ブランチ {r.default_branch}
              </span>
              <ExternalLink className="size-3.5 shrink-0 text-ink-3" />
            </li>
          ))}
        </ul>
      )}
      {isOwner && (
        <Panel>
          <PanelTitle>リポジトリを追加</PanelTitle>
          <ol className="mt-4 space-y-5">
            <li className="flex gap-3">
              <StepNumber n={1} />
              <div className="min-w-0 flex-1 space-y-2">
                <p className="text-sm font-medium">
                  SpecLino の GitHub App を対象のリポジトリにインストールする
                </p>
                <p className="text-xs text-ink-3">
                  GitHub
                  の画面で、インストール先のアカウントとリポジトリを選びます。済んでいれば次へ。
                </p>
                <Button asChild className="bg-brand text-white">
                  <a
                    href={GITHUB_APP_INSTALL_URL}
                    target="_blank"
                    rel="noreferrer"
                  >
                    GitHub App をインストール
                    <ExternalLink className="size-3.5" />
                  </a>
                </Button>
              </div>
            </li>
            <li className="flex gap-3">
              <StepNumber n={2} />
              <div className="min-w-0 flex-1 space-y-2">
                <p className="text-sm font-medium">リポジトリを登録する</p>
                <p className="text-xs text-ink-3">
                  GitHub で本人確認し、あなたがそのリポジトリの admin で、App
                  がインストールされていることを確かめてから登録します。
                </p>
                <form
                  className="flex flex-wrap items-end gap-3"
                  onSubmit={handleAdd}
                >
                  <div className="grid min-w-[16rem] flex-1 gap-1.5">
                    <Label htmlFor="repo-name">リポジトリ</Label>
                    <Input
                      id="repo-name"
                      value={repoName}
                      onChange={(e) => setRepoName(e.target.value)}
                      placeholder="owner/name"
                      autoComplete="off"
                    />
                  </div>
                  <Button type="submit" variant="outline" disabled={starting}>
                    GitHub で確認して追加
                  </Button>
                </form>
                {startError && <p className="text-sm text-bad">{startError}</p>}
              </div>
            </li>
          </ol>
        </Panel>
      )}
    </div>
  );
}

function StepNumber({ n }: { n: number }) {
  return (
    <span className="flex size-6 shrink-0 items-center justify-center rounded-full bg-primary/10 text-xs font-semibold text-primary">
      {n}
    </span>
  );
}
