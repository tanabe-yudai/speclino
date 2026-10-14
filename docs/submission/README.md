# ハッカソン提出

第5回 Agentic AI Hackathon with Google Cloud の提出情報。**審査員向けの操作手順は [judge-guide.md](judge-guide.md)**（本番の画面と GitHub のデモ PR）。手元で DB から作り直す通し手順は [../demo-knowledge-registry.md](../demo-knowledge-registry.md)。

## 提出情報

| 項目 | 内容 |
| --- | --- |
| デモ URL | https://speclino-web-6h5m3svkja-an.a.run.app |
| テストアカウント | 提出フォームにだけ記載（メール / パスワード。ソーシャルログイン不要）。リポジトリには置かない |
| デモ PR（12/1 まで open） | [#1](https://github.com/tanabe-yudai/speclino/pull/1) デモ1: テストは緑でもゲートの図に違反（反例つき）／[#2](https://github.com/tanabe-yudai/speclino/pull/2) デモ3: 仕様の写しを消しても lock の照合で止まる／[#3](https://github.com/tanabe-yudai/speclino/pull/3) デモ4: 解析できないループは UNKNOWN でゲート失敗 |
| デモ動画 | TODO（提出前に記載）。台本は [video-script.md](video-script.md) |
| Zenn 記事 | TODO（提出前に記載）。下書きは [zenn-draft.md](zenn-draft.md) |
| 提出フォームの回答 | [form.md](form.md) |

## 12/1 までデプロイを維持する予算計画

費用の大きい順に、構成と上限を Terraform（`infra/envs/prod/`）とアプリの設定で固定している。
単価は 2026-10-13 に公式の料金ページ（[Cloud Run](https://cloud.google.com/run/pricing)・
[Cloud SQL](https://cloud.google.com/sql/pricing)）で確かめた USD の表示価格。東京（asia-northeast1）の
Cloud SQL の単価は公式ページの静的な表で確かめられなかったので**概算**。

| 項目 | 構成（正本） | 費用の見込み（月） | 上限・止め方 |
| --- | --- | --- | --- |
| Cloud Run web | 1 vCPU・1 GiB、**min 1**（起動待ちを避ける）、max 2（`run.tf`） | **約 $13**: 待機（idle の最小インスタンス）は東京も Tier 1 で vCPU $0.0000025/秒・メモリ $0.0000025/GiB 秒 → 1 vCPU＋1 GiB × 30 日 = $12.96。リクエストを処理した時間は別（数ドル以内の見込み、無料枠あり） | max-instances=2 |
| Cloud Run worker / sandbox | min 0、max 2。sandbox は同時実行 1 | 実行した分だけ（待機の費用なし） | Cloud Tasks のキュー、max-instances=2 |
| Cloud SQL | `db-f1-micro`・HDD 10GB・ゾーン・バックアップなし（[`guides/deploy-runbook.md`](../guides/deploy-runbook.md) の 4 章。Terraform 外） | **約 $11（概算）**: インスタンスは us-central1 で $0.0105/時（≒ $7.7）、東京は約 $10。HDD は us-central1 で $0.000123288/GiB 時 × 10GB ≒ $0.9 | 常時起動。止めるとサービスが止まる |
| Vertex AI（Gemini） | 知識のチャット・仕様の提案・LLM 支援の規約・調査エージェント・A2A | 日次の上限まで（最大 $3 × 日数） | `SPECLINO_DAILY_COST_LIMIT_USD`（既定 $3/日、全用途で共有）。超えたら起動しない。LLM 支援は記録を再利用し、同じコードでは呼ばない |
| 再実行 | 画面からの再検査 | 上の LLM の枠に含む | `SPECLINO_DEMO_RERUN_LIMIT_PER_DAY`（既定 20 回/日） |
| GCS・Cloud Tasks・Logging・Secret Manager・Artifact Registry | 既定の構成 | 数ドル以内（概算。Artifact Registry はイメージが溜まるほど増える） | 古いイメージを消す |

- **予算アラート**: `budget.tf` で予算（既定 45,000 円 ≒ $300）の 17% / 33% / 67% / 100%（≒ $50 / $100 / $200 / $300）に
  通知する（実際の支出ベース）
- **見込み**: 固定費（web の待機と Cloud SQL）は**月約 $24**、10/15〜12/1（47 日）で**約 $38**。その他の少額を足しても
  $45 前後。LLM は日次の上限 $3 で最大 **$141**（47 日）。合わせて**最大でも約 $190**で、クーポン（$300）の残りに収まる見込み。
  ただし 9/28 からの実績（開発中の LLM・検査）がすでにクーポンを使っているので、10/14 時点の請求で残額を確かめる
- **クーポンの期限に注意**: $300 のクーポンは 2026-09-28 頃に登録し、有効期間は 2 か月。**11 月下旬に切れ、12/1 より前に
  切れる可能性がある**。切れた後の支出は課金アカウントに請求される（固定費だけなら数日で数ドル）。期限の正確な日付は
  請求の画面で確かめる
- **超えそうなとき**: (1) `SPECLINO_AGENT_ENABLED` / `SPECLINO_LLM_POLICIES_ENABLED` / `SPECLINO_SPEC_BOT_ENABLED` を false にする
  （LLM 支援の規約は UNKNOWN になり、ゲートは安全側に倒れる）、(2) 日次の上限を下げる、(3) web の min-instances を 0 に戻す
  （月 $13 減る代わりに、起動待ちが 12〜21 秒に戻る）。判定と画面は LLM 無しでも動く
- 12/1 以降に畳むときは [guides/deploy-runbook.md](../guides/deploy-runbook.md) の手順で Cloud SQL を先に止める。
  CLI の自己検証は GCP が無くても回る
