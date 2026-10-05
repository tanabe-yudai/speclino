#!/usr/bin/env bash
# ストーリー（Issue）と GitHub Project「SpecLino」の進捗を同期する薄いラッパ。
#   story.sh board                 In Progress / Blocked と件数を表示
#   story.sh next                  依存が全て Done の未着手ストーリーを優先度順に表示
#   story.sh start <issue>         Status=In Progress、着手コメント
#   story.sh done <issue> [-r file] 「## 成果」に追記、Status=Done、Issue をクローズ
#   story.sh block <issue> <理由>   Status=Blocked、blocked ラベル、理由コメント
#   story.sh show <issue>          Issue 本文を表示
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CFG="$ROOT/.github/project.json"
# project.json をドット区切りのキーで引く（eval は使わない）
cfg() { python3 -c "
import json,sys
d=json.load(open(sys.argv[1]))
for k in sys.argv[2].split('.'): d=d[k]
print(d)" "$CFG" "$1"; }
REPO="$(cfg repo)"; OWNER="$(cfg owner)"; PN="$(cfg project_number)"; PID="$(cfg project_id)"
STATUS_FIELD="$(cfg fields.Status.id)"

items_json() { gh project item-list "$PN" --owner "$OWNER" --limit 200 --format json; }

item_id_of() { # <issue number> -> project item id
  # item-add 直後は item-list（先頭 200 件）に出ないことがあるので、Issue から直接引く。
  # 引けなければ（API 失敗を含む）item-list にフォールバックする
  local id
  id="$(gh api graphql -F owner="${REPO%%/*}" -F name="${REPO##*/}" -F number="$1" -f query='
    query($owner:String!,$name:String!,$number:Int!){
      repository(owner:$owner,name:$name){ issue(number:$number){
        projectItems(first:20){ nodes{ id project{ id } } } } } }' \
    --jq ".data.repository.issue.projectItems.nodes[] | select(.project.id==\"$PID\") | .id" 2>/dev/null | grep -m1 "^PVTI_")" || true
  if [ -n "$id" ]; then echo "$id"; return; fi
  items_json | python3 -c "
import json,sys
n=int(sys.argv[1])
for it in json.load(sys.stdin)['items']:
    c=it.get('content') or {}
    if c.get('number')==n: print(it['id']); break
" "$1"
}

set_status() { # <issue> <Todo|In Progress|Blocked|Done>
  local iid opt
  iid="$(item_id_of "$1")"; [ -n "$iid" ] || { echo "issue #$1 は Project に無い" >&2; exit 1; }
  opt="$(cfg "fields.Status.options.$2")"
  gh project item-edit --project-id "$PID" --id "$iid" --field-id "$STATUS_FIELD" --single-select-option-id "$opt" >/dev/null
}

cmd="${1:-}"; shift || true
case "$cmd" in
  board)
    items_json | python3 -c "
import json,sys,collections
items=json.load(sys.stdin)['items']
cnt=collections.Counter(it.get('status','?') for it in items)
print('件数:', dict(cnt))
for st in ('In Progress','Blocked'):
    print(f'--- {st}')
    for it in items:
        if it.get('status')==st:
            c=it.get('content') or {}
            print(f\"  #{c.get('number')} {c.get('title')}  [{it.get('priority','-')}] deps={it.get('depends on','-')}\")
";;
  next)
    items_json | python3 -c "
import json,sys,re
items=json.load(sys.stdin)['items']
done={ (it.get('content') or {}).get('number') for it in items if it.get('status')=='Done' or (it.get('content') or {}).get('state')=='CLOSED'}
pr={'P0':0,'P1':1,'P2':2}
cands=[]
for it in items:
    c=it.get('content') or {}
    if it.get('status') in ('Done','In Progress') or c.get('state')=='CLOSED': continue
    deps=[int(x) for x in re.findall(r'#(\d+)', it.get('depends on') or '')]
    if all(d in done for d in deps):
        cands.append((pr.get(it.get('priority'),9), it.get('milestone') or {}, c.get('number'), c.get('title'), it.get('priority'), it.get('status')))
cands.sort(key=lambda t:(t[0], (t[1] or {}).get('title','zz'), t[2]))
print('着手可能（依存が全て Done）:')
for p,ms,n,t,prio,st in cands:
    print(f'  #{n} {t}  [{prio}] {(ms or {}).get(\"title\",\"\")}  status={st}')
";;
  start)
    n="${1:?issue number}"
    set_status "$n" "In Progress"
    gh issue comment "$n" --repo "$REPO" --body "着手（$(date +%Y-%m-%d\ %H:%M)）" >/dev/null
    echo "#$n → In Progress";;
  done)
    n="${1:?issue number}"; shift || true
    result=""; if [ "${1:-}" = "-r" ]; then result="$(cat "$2")"; fi
    [ -n "$result" ] || result="$(cat)"   # 標準入力からも受け取れる
    body="$(gh issue view "$n" --repo "$REPO" --json body --jq .body)"
    newbody="$(python3 -c "
import sys,re
body=sys.stdin.read(); res=open(sys.argv[1]).read().strip()
stamp='$(date +%Y-%m-%d)'
if '## 成果' in body:
    head,_,tail=body.partition('## 成果')
    body=head+'## 成果\n\n'+res+'\n'
else:
    body=body.rstrip()+'\n\n## 成果\n\n'+res+'\n'
print(body)
" <(printf '%s' "$result") <<<"$body")"
    gh issue edit "$n" --repo "$REPO" --body "$newbody" >/dev/null
    set_status "$n" "Done"
    gh issue close "$n" --repo "$REPO" --comment "完了。成果は本文の「## 成果」を参照。" >/dev/null
    echo "#$n → Done";;
  block)
    n="${1:?issue number}"; reason="${2:?理由}"
    set_status "$n" "Blocked"
    gh issue edit "$n" --repo "$REPO" --add-label blocked >/dev/null
    gh issue comment "$n" --repo "$REPO" --body "Blocked: $reason" >/dev/null
    echo "#$n → Blocked";;
  show)
    gh issue view "${1:?issue number}" --repo "$REPO";;
  *)
    sed -n '2,9p' "$0"; exit 1;;
esac
