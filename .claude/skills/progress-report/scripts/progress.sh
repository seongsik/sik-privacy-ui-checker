#!/usr/bin/env bash
# progress.sh — plan-checkbox based progress for the progress-report skill
# (sik-sdd-foundation). bash 3.2 compatible; needs git, awk, sed, grep, cmp.
#
# usage: progress.sh done <plan-file> <task-number> | status [--tsv] | report | check
# Reads docs/plans/*.md and docs/superpowers/plans/*.md (files with "### Task N:"),
# docs/requirements/REQUIREMENTS.md, docs/specs/*.md, docs/superpowers/specs/*.md.
# Writes only under docs/. See docs/specs/2026-10-24-sdd-extension.md in the standard.
set -euo pipefail

USAGE="usage: progress.sh done <plan-file> <task-number> | status [--tsv] | report | check [--ci] | baseline"
die() { echo "progress.sh: $*" >&2; exit 2; }

ROOT=$(git rev-parse --show-toplevel 2>/dev/null) || die "not inside a git repository"
cd "$ROOT"
PROJECT=$(basename "$ROOT")
REQ_FILE=docs/requirements/REQUIREMENTS.md
CHANGES_FILE=docs/requirements/CHANGES.md
BASELINE_FILE=docs/requirements/BASELINE.md   # snapshot of REQ_FILE written by `baseline`
BASELINE_REF=""   # set by collect: the snapshot's creation date, "" when there is no baseline
STATUS_FILE=docs/progress/status.json
TRACE_FILE=docs/traceability.md
REPORT_DIR=docs/progress
TEST_DIR=docs/test-reports
TODAY=$(date +%Y-%m-%d)
STD_VERSION=$( { tr -d '[:space:]' < .claude/shared/VERSION; } 2>/dev/null || true)
[ -n "$STD_VERSION" ] || STD_VERSION=unknown

TMP=$(mktemp -d "${TMPDIR:-/tmp}/progress.XXXXXX")
trap 'rm -rf "$TMP"' EXIT

# ---- collection -------------------------------------------------------------
# tasks.tsv   plan  n  title  reqs  weight  steps_total  steps_done  done(0/1)
# plans.tsv   plan  title  spec  approved
# reqs.tsv    id  title  priority  status
# specs.tsv   spec  reqs  approved
# commits.tsv id  count
# changes.tsv cr  date  requester  reason  reqs(csv, no spaces)  artifacts  status  approver
# reqdiff.tsv id  added|changed|removed   (requirement rows that differ from the baseline)

plan_files() {
  local f
  for f in docs/plans/*.md docs/superpowers/plans/*.md; do
    [ -f "$f" ] || continue
    if grep -q '^### Task [0-9][0-9]*:' "$f"; then printf '%s\n' "$f"; fi
  done
}

spec_files() {
  local f
  for f in docs/specs/*.md docs/superpowers/specs/*.md; do
    [ -f "$f" ] && printf '%s\n' "$f"
  done
  return 0
}

# no_symlink <path> : refuse to write through a symlinked file or parent directory.
# Every write target is derived from repository content, so a hostile checkout
# must not be able to redirect an append outside the working tree.
no_symlink() {
  local p=$1 d
  [ -L "$p" ] && die "refusing to write through symlink: $p"
  d=$(dirname "$p")
  while [ "$d" != "." ] && [ "$d" != "/" ]; do
    [ -L "$d" ] && die "refusing to write through symlinked directory: $d"
    d=$(dirname "$d")
  done
  return 0
}

# header_field <file> <label> : value of a "**label:** value" line (first match)
header_field() {
  sed -n "s/^\*\*$2:\*\*[[:space:]]*//p" "$1" | head -n 1 | tr -d '\r' | tr '\t' ' ' | sed 's/[[:space:]]*$//'
}

# plan_title <file> : first "# " heading, without CR/tabs (they would break the TSV)
plan_title() {
  sed -n 's/^# //p' "$1" | head -n 1 | tr -d '\r' | tr '\t' ' ' | sed 's/[[:space:]]*$//'
}

# approval <file> : "**승인:**" or "**Approved:**"
approval() {
  local a; a=$(header_field "$1" "승인")
  [ -n "$a" ] || a=$(header_field "$1" "Approved")
  printf '%s' "$a"
}

# scan_plan_tasks <plan> : one tasks.tsv line per "### Task N:" heading.
# Lines inside code fences are ignored (plans quote Markdown in code blocks). A fence
# closes only on a fence at least as long as the opening one (CommonMark), so a
# ``` block nested in a ```` block stays inside.
scan_plan_tasks() {
  awk -v plan="$1" '
    function flush() {
      if (intask) printf "%s\t%s\t%s\t%s\t%d\t%d\t%d\t%d\n", plan, n, title, reqs, w, st, sd, (st > 0 && st == sd) ? 1 : 0
      intask = 0; st = 0; sd = 0
    }
    { sub(/\r$/, "") }
    /^```/ { match($0, /^`+/); if (!flen) flen = RLENGTH; else if (RLENGTH >= flen) flen = 0; next }
    flen { next }
    /^### Task [0-9]+:/ {
      flush(); intask = 1
      n = $0; sub(/^### Task /, "", n); sub(/:.*/, "", n)
      title = $0; sub(/^### Task [0-9]+:[ ]*/, "", title)
      gsub(/\t/, " ", title); sub(/[ ]+$/, "", title)
      reqs = ""; w = 1
      while (match(title, /\[[^]]*\][ ]*$/)) {
        tag = substr(title, RSTART + 1); sub(/\].*$/, "", tag)
        rest = substr(title, 1, RSTART - 1); sub(/[ ]+$/, "", rest)
        if (tag ~ /^w=[0-9]+$/) { w = substr(tag, 3) + 0; if (w < 1) w = 1 }
        else if (tag ~ /^REQ-[0-9]+([ ]*,[ ]*REQ-[0-9]+)*$/) { gsub(/ /, "", tag); reqs = (reqs == "" ? tag : tag "," reqs) }
        else break
        title = rest
      }
      next
    }
    intask && /^## / { flush() }
    intask && /^- \[[ xX]\] / { st++; if ($0 ~ /^- \[[xX]\]/) sd++ }
    END { flush() }
  ' "$1"
}

# find_workspace <plan> : Superpowers SDD workspace dir for this plan, or empty
find_workspace() {
  local d
  for d in .superpowers/sdd/*/; do
    [ -f "$d/plan-path" ] || continue
    if [ "$(cat "$d/plan-path")" = "$1" ]; then printf '%s' "${d%/}"; return 0; fi
  done
  d=".superpowers/sdd/$(basename "$1" .md)"
  if [ -d "$d" ] && [ ! -f "$d/plan-path" ]; then printf '%s' "$d"; fi
  return 0
}

# req_rows <file> : "ID<TAB>normalized row" per requirement row (cells trimmed, inner
# whitespace collapsed) so that whitespace-only edits do not count as changes.
req_rows() {
  awk -F'|' '{ sub(/\r$/, "") } /^\| *REQ-[0-9]+ *\|/ {
    row = ""
    for (i = 2; i < NF; i++) { c = $i; gsub(/^[ \t]+/, "", c); gsub(/[ \t]+$/, "", c); gsub(/[ \t]+/, " ", c); row = row (i > 2 ? "|" : "") c }
    id = $2; gsub(/[ \t]/, "", id); print id "\t" row }' "$1"
}

# collect_changes : changes.tsv from CHANGES.md, reqdiff.tsv from baseline vs current
collect_changes() {
  : > "$TMP/changes.tsv"; : > "$TMP/reqdiff.tsv"; rm -f "$TMP/baseline_legacy"
  if [ -f "$CHANGES_FILE" ]; then
    awk -F'|' '{ sub(/\r$/, "") } /^\| *CR-[0-9]+ *\|/ {
      for (i = 2; i <= 9; i++) { gsub(/^[ \t]+/, "", $i); gsub(/[ \t]+$/, "", $i); gsub(/\t/, " ", $i) }
      gsub(/ /, "", $6)
      printf "%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n", $2, $3, $4, $5, $6, $7, $8, $9 }' "$CHANGES_FILE" > "$TMP/changes.tsv"
  fi
  [ -f "$REQ_FILE" ] || return 0
  # The baseline is a snapshot file, not a commit: it survives squash merges, rebases and
  # shallow clones. A 1.6.x header that still points at a commit is reported as legacy.
  if [ ! -f "$BASELINE_FILE" ]; then
    if header_field "$REQ_FILE" "기준선" | grep -q '^[0-9a-f]\{7,40\}'; then : > "$TMP/baseline_legacy"; fi
    return 0
  fi
  BASELINE_REF=$(sed -n 's/^<!-- *기준선 생성: *\([0-9][0-9-]*\).*/\1/p' "$BASELINE_FILE" | head -n 1)
  [ -n "$BASELINE_REF" ] || BASELINE_REF="unknown"
  req_rows "$BASELINE_FILE" > "$TMP/req_old.tsv"
  req_rows "$REQ_FILE" > "$TMP/req_new.tsv"
  awk -F'\t' -v oldf="$TMP/req_old.tsv" '
    FILENAME == oldf { old[$1] = $2; next }
    { new[$1] = $2 }
    END {
      for (k in new) if (!(k in old)) print k "\tadded"; else if (old[k] != new[k]) print k "\tchanged"
      for (k in old) if (!(k in new)) print k "\tremoved"
    }' "$TMP/req_old.tsv" "$TMP/req_new.tsv" | sort > "$TMP/reqdiff.tsv"
}

collect() {
  : > "$TMP/tasks.tsv"; : > "$TMP/plans.tsv"; : > "$TMP/reqs.tsv"; : > "$TMP/specs.tsv"; : > "$TMP/commits.tsv"
  collect_changes
  local f title id
  while IFS= read -r f; do
    title=$(plan_title "$f")
    printf '%s\t%s\t%s\t%s\n' "$f" "$title" "$(header_field "$f" Spec)" "$(approval "$f")" >> "$TMP/plans.tsv"
    scan_plan_tasks "$f" >> "$TMP/tasks.tsv"
  done < <(plan_files)
  while IFS= read -r f; do
    printf '%s\t%s\t%s\n' "$f" "$(header_field "$f" "요구사항" | tr -d ' \t')" "$(approval "$f")" >> "$TMP/specs.tsv"
  done < <(spec_files)
  if [ -f "$REQ_FILE" ]; then
    awk -F'|' '{ sub(/\r$/, "") } /^\| *REQ-[0-9]+ *\|/ {
      for (i = 2; i <= 6; i++) { gsub(/^[ \t]+/, "", $i); gsub(/[ \t]+$/, "", $i); gsub(/\t/, " ", $i) }
      printf "%s\t%s\t%s\t%s\n", $2, $3, $5, $6 }' "$REQ_FILE" > "$TMP/reqs.tsv"
  fi
  while IFS=$'\t' read -r id _; do
    [ -n "$id" ] || continue
    printf '%s\t%s\n' "$id" "$(git log -E --grep="^Refs:.*${id}([^0-9]|\$)" --format=%h 2>/dev/null | wc -l | tr -d ' ')" >> "$TMP/commits.tsv"
  done < "$TMP/reqs.tsv"
}

# ---- awk model --------------------------------------------------------------
# The awk programs below are single-quoted on purpose: $1, $5 ... are awk fields.
# shellcheck disable=SC2016
AWK_LOAD='
FILENAME ~ /tasks\.tsv$/ {
  nt++; tp[nt] = $1; tn[nt] = $2; tt[nt] = $3; tr[nt] = $4; tw[nt] = $5; ts[nt] = $6; tsd[nt] = $7; td[nt] = $8
  pt[$1]++; pw[$1] += $5; ps[$1] += $6; psd[$1] += $7
  if ($8) { ptd[$1]++; pwd[$1] += $5 }
  if ($4 == "") untagged++
  m = split($4, a, ",")
  for (i = 1; i <= m; i++) {
    rt[a[i]]++; rw[a[i]] += $5; if ($8) { rtd[a[i]]++; rwd[a[i]] += $5 }
    if (!index("," rpl[a[i]] ",", "," $1 ",")) rpl[a[i]] = (rpl[a[i]] == "" ? $1 : rpl[a[i]] "," $1)
    if (!index("," preq[$1] ",", "," a[i] ",")) preq[$1] = (preq[$1] == "" ? a[i] : preq[$1] "," a[i])
  }
  next
}
FILENAME ~ /plans\.tsv$/ { np++; pf[np] = $1; ptitle[$1] = $2; pspec[$1] = $3; pappr[$1] = $4; next }
FILENAME ~ /reqs\.tsv$/ { nr++; rid[nr] = $1; rtitle[$1] = $2; rprio[$1] = $3; rstat[$1] = $4; known[$1] = 1; next }
FILENAME ~ /specs\.tsv$/ {
  ns++; sf[ns] = $1; sreqs[$1] = $2; sappr[$1] = $3
  m = split($2, a, ","); for (i = 1; i <= m; i++) if (a[i] != "") rsp[a[i]] = (rsp[a[i]] == "" ? $1 : rsp[a[i]] "," $1)
  next
}
FILENAME ~ /commits\.tsv$/ { rc[$1] = $2; next }
FILENAME ~ /changes\.tsv$/ {
  nc++; cid[nc] = $1; cdate[$1] = $2; cwho[$1] = $3; creason[$1] = $4; creqs[$1] = $5; cart[$1] = $6; cstat[$1] = $7; cappr[$1] = $8
  if ($7 == "승인") { ca++; m = split($5, a, ","); for (i = 1; i <= m; i++) if (a[i] != "") crfor[a[i]] = (crfor[a[i]] == "" ? $1 : crfor[a[i]] "," $1) }
  else if ($7 == "제안") cp++
  else if ($7 == "기각") cj++
  next
}
FILENAME ~ /reqdiff\.tsv$/ { nd++; did[nd] = $1; dkind[$1] = $2; next }
function pct(d, t)     { return t > 0 ? sprintf("%.1f", 100 * d / t) : "null" }
function pctcell(d, t) { return t > 0 ? sprintf("%.1f%%", 100 * d / t) : "-" }
# jesc: JSON-escape backslash and quote. Char loop, not gsub: macOS awk keeps "\\" in
# gsub replacements literally while gawk collapses it, so gsub is not portable here.
# Control characters (CR, ESC, ...) are dropped: raw ones would make the JSON invalid.
function jesc(s,   out, i, c) {
  gsub(/[[:cntrl:]]/, "", s)
  out = ""
  for (i = 1; i <= length(s); i++) { c = substr(s, i, 1); out = out ((c == "\\" || c == "\"") ? "\\" c : c) }
  return out
}
function jarr(csv,   m, a, i, out) {
  m = split(csv, a, ","); out = ""
  for (i = 1; i <= m; i++) if (a[i] != "") out = out (out == "" ? "" : ", ") "\"" jesc(a[i]) "\""
  return "[" out "]"
}
function totals() { WT = 0; WD = 0; TD = 0; for (i = 1; i <= nt; i++) { WT += tw[i]; if (td[i]) { WD += tw[i]; TD++ } } }
# print_untracked(prefix): prints one line per untracked item, returns the count
function print_untracked(prefix,   i, j, m, a, u) {
  u = 0
  for (i = 1; i <= nr; i++) if (rt[rid[i]] == 0 && rsp[rid[i]] == "") { printf "%s설계서·계획서가 없는 요구사항: %s\n", prefix, rid[i]; u++ }
  for (i = 1; i <= nt; i++) if (tr[i] == "") { printf "%s요구사항 태그 없는 태스크: %s Task %s (%s)\n", prefix, tp[i], tn[i], tt[i]; u++ }
  for (i = 1; i <= nt; i++) { m = split(tr[i], a, ","); for (j = 1; j <= m; j++) if (a[j] != "" && !known[a[j]]) { printf "%s정의되지 않은 요구사항 참조: %s Task %s → %s\n", prefix, tp[i], tn[i], a[j]; u++ } }
  for (i = 1; i <= ns; i++) { m = split(sreqs[sf[i]], a, ","); for (j = 1; j <= m; j++) if (a[j] != "" && !known[a[j]]) { printf "%s정의되지 않은 요구사항 참조: %s → %s\n", prefix, sf[i], a[j]; u++ } }
  return u
}
function untracked(   i, j, m, a, u) {
  u = untagged + 0
  for (i = 1; i <= nr; i++) if (rt[rid[i]] == 0 && rsp[rid[i]] == "") u++
  for (i = 1; i <= nt; i++) { m = split(tr[i], a, ","); for (j = 1; j <= m; j++) if (a[j] != "" && !known[a[j]]) u++ }
  for (i = 1; i <= ns; i++) { m = split(sreqs[sf[i]], a, ","); for (j = 1; j <= m; j++) if (a[j] != "" && !known[a[j]]) u++ }
  return u
}
'

AWK_JSON='
END {
  totals()
  printf "{\n  \"schema\": 1,\n  \"project\": \"%s\",\n  \"generated_at\": \"%s\",\n  \"standard_version\": \"%s\",\n", jesc(project), today, jesc(stdver)
  printf "  \"requirements_baseline\": %s,\n  \"changes\": {\"approved\": %d, \"proposed\": %d, \"rejected\": %d},\n", (baseline == "" ? "null" : "\"" jesc(baseline) "\""), ca + 0, cp + 0, cj + 0
  printf "  \"overall\": {\"progress\": %s, \"tasks_done\": %d, \"tasks_total\": %d, \"weight_done\": %d, \"weight_total\": %d, \"untracked\": %d},\n", pct(WD, WT), TD, nt, WD, WT, untracked()
  printf "  \"plans\": ["
  for (i = 1; i <= np; i++) { p = pf[i]
    printf "%s\n    {\"file\": \"%s\", \"title\": \"%s\", \"spec\": \"%s\", \"approved\": \"%s\", \"requirements\": %s, \"tasks_done\": %d, \"tasks_total\": %d, \"weight_done\": %d, \"weight_total\": %d, \"progress\": %s}", (i > 1 ? "," : ""), jesc(p), jesc(ptitle[p]), jesc(pspec[p]), jesc(pappr[p]), jarr(preq[p]), ptd[p] + 0, pt[p] + 0, pwd[p] + 0, pw[p] + 0, pct(pwd[p], pw[p]) }
  printf "%s],\n  \"requirements\": [", (np > 0 ? "\n  " : "")
  for (i = 1; i <= nr; i++) { r = rid[i]
    printf "%s\n    {\"id\": \"%s\", \"title\": \"%s\", \"priority\": \"%s\", \"status\": \"%s\", \"progress\": %s, \"tasks_done\": %d, \"tasks_total\": %d, \"commits\": %d, \"plans\": %s}", (i > 1 ? "," : ""), r, jesc(rtitle[r]), jesc(rprio[r]), jesc(rstat[r]), pct(rwd[r], rw[r]), rtd[r] + 0, rt[r] + 0, rc[r] + 0, jarr(rpl[r]) }
  printf "%s]\n}\n", (nr > 0 ? "\n  " : "")
}
'

AWK_TRACE='
END {
  printf "# 추적 매트릭스\n\n<!-- progress.sh status 가 생성한다. 직접 편집하지 않는다. -->\n\n생성일: %s\n\n", today
  printf "| 요구사항 | 제목 | 상태 | 설계서 | 계획서 (완료/전체 태스크) | 커밋 | 진행률 |\n|---|---|---|---|---|---|---|\n"
  for (i = 1; i <= nr; i++) { r = rid[i]
    pl = rpl[r]; gsub(/,/, "<br>", pl); sp = rsp[r]; gsub(/,/, "<br>", sp)
    printf "| %s | %s | %s | %s | %s (%d/%d) | %d | %s |\n", r, rtitle[r], rstat[r], (sp == "" ? "-" : sp), (pl == "" ? "-" : pl), rtd[r] + 0, rt[r] + 0, rc[r] + 0, pctcell(rwd[r], rw[r]) }
  printf "\n## 미추적\n\n"
  if (print_untracked("- ") == 0) printf "- 없음\n"
}
'

AWK_TSV='
END {
  totals()
  printf "project\tstandard_version\tplans\ttasks_done\ttasks_total\tweight_done\tweight_total\tprogress\tuntracked\tgenerated_at\n"
  printf "%s\t%s\t%d\t%d\t%d\t%d\t%d\t%s\t%d\t%s\n", project, stdver, np, TD, nt, WD, WT, pct(WD, WT), untracked(), today
}
'

# Previous overall progress (from the committed status.json) arrives as $PREV.
# shellcheck disable=SC2016  # backticks inside are Markdown
AWK_REPORT='
END {
  totals()
  cur = pct(WD, WT); prev = ENVIRON["PREV"]
  delta = (prev == "" || prev == "null" || cur == "null") ? "-" : sprintf("%+.1f%%p", cur - prev)
  printf "# 공정률 보고서 (%s)\n\n<!-- progress.sh report 가 생성한다. -->\n\n", today
  printf "## 요약\n\n| 항목 | 값 |\n|---|---|\n| 프로젝트 | %s |\n| 공정률 (가중) | %s |\n| 직전 commit 대비 | %s |\n| 태스크 완료/전체 | %d/%d |\n| 계획서 | %d |\n| 미추적 항목 | %d |\n\n", project, (cur == "null" ? "-" : cur "%"), delta, TD, nt, np, untracked()
  printf "## 계획서별\n\n| 계획서 | 승인 | 요구사항 | 태스크 완료/전체 | 가중 완료/전체 | Step 완료/전체 | 공정률 |\n|---|---|---|---|---|---|---|\n"
  for (i = 1; i <= np; i++) { p = pf[i]
    printf "| %s (`%s`) | %s | %s | %d/%d | %d/%d | %d/%d | %s |\n", (ptitle[p] == "" ? "-" : ptitle[p]), p, (pappr[p] == "" ? "-" : pappr[p]), (preq[p] == "" ? "-" : preq[p]), ptd[p] + 0, pt[p] + 0, pwd[p] + 0, pw[p] + 0, psd[p] + 0, ps[p] + 0, pctcell(pwd[p], pw[p]) }
  printf "\n## 요구사항별\n\n| ID | 제목 | 우선순위 | 상태 | 태스크 완료/전체 | 커밋 | 공정률 |\n|---|---|---|---|---|---|---|\n"
  for (i = 1; i <= nr; i++) { r = rid[i]
    printf "| %s | %s | %s | %s | %d/%d | %d | %s |\n", r, rtitle[r], rprio[r], rstat[r], rtd[r] + 0, rt[r] + 0, rc[r] + 0, pctcell(rwd[r], rw[r]) }
  printf "\n## 변경요청\n\n기준선: %s\n\n", (baseline == "" ? "없음 (progress.sh baseline)" : baseline)
  printf "| CR | 일자 | 요청자 | 사유 | 영향 요구사항 | 영향 산출물 | 상태 | 승인 |\n|---|---|---|---|---|---|---|---|\n"
  for (i = 1; i <= nc; i++) { c = cid[i]; printf "| %s | %s | %s | %s | %s | %s | %s | %s |\n", c, cdate[c], cwho[c], creason[c], creqs[c], cart[c], cstat[c], cappr[c] }
  if (nc == 0) printf "| - | | | | | | | |\n"
  printf "\n기준선 이후 변경된 요구사항:\n\n"
  for (i = 1; i <= nd; i++) printf "- %s (%s) — %s\n", did[i], dkind[did[i]], (crfor[did[i]] == "" ? "승인된 변경요청 없음" : crfor[did[i]])
  if (nd == 0) printf "- 없음\n"
  printf "\n## 미추적\n\n"
  if (print_untracked("- ") == 0) printf "- 없음\n"
}
'

AWK_WARN='
END { print_untracked("") }
'

# run_awk <body> : AWK_LOAD + body over the five collected files
run_awk() {
  awk -F'\t' -v project="$PROJECT" -v today="$TODAY" -v stdver="$STD_VERSION" -v baseline="$BASELINE_REF" "$AWK_LOAD $1" \
    "$TMP/tasks.tsv" "$TMP/plans.tsv" "$TMP/reqs.tsv" "$TMP/specs.tsv" "$TMP/commits.tsv" "$TMP/changes.tsv" "$TMP/reqdiff.tsv"
}

# write_if_changed <new> <dest> : skip when only generated_at / 생성일 differ
write_if_changed() {
  if [ -f "$2" ] && cmp -s <(grep -v -e '"generated_at"' -e '^생성일:' "$1") <(grep -v -e '"generated_at"' -e '^생성일:' "$2"); then
    return 0
  fi
  no_symlink "$2"
  mkdir -p "$(dirname "$2")"
  cat "$1" > "$2"
  echo "wrote $2"
}

# ---- commands ---------------------------------------------------------------
cmd_status() {
  collect
  if [ "${1:-}" = "--tsv" ]; then run_awk "$AWK_TSV"; return 0; fi
  run_awk "$AWK_JSON" > "$TMP/status.json"
  run_awk "$AWK_TRACE" > "$TMP/trace.md"
  write_if_changed "$TMP/status.json" "$STATUS_FILE"
  write_if_changed "$TMP/trace.md" "$TRACE_FILE"
}

cmd_done() {
  local plan=$1 n=$2 line title reqs commits tests ws l rep
  case "$plan" in "$ROOT"/*) plan=${plan#"$ROOT"/} ;; esac
  case "$plan" in /*|../*|*/../*|*/..) die "plan must be a path inside the repository: $plan" ;; esac
  [ -f "$plan" ] || die "no such plan file: $plan"
  no_symlink "$plan"
  case "$n" in ''|*[!0-9]*) die "task number must be an integer: $n" ;; esac
  line=$(scan_plan_tasks "$plan" | awk -F'\t' -v n="$n" '$2 == n')
  [ -n "$line" ] || die "Task $n not found in $plan"
  rep="$TEST_DIR/$(basename "$plan" .md).md"
  no_symlink "$rep"
  awk -v n="$n" '
    /^```/ { match($0, /^`+/); if (!flen) flen = RLENGTH; else if (RLENGTH >= flen) flen = 0; print; next }
    flen { print; next }
    /^### Task [0-9]+:/ { t = $0; sub(/^### Task /, "", t); sub(/:.*/, "", t); intask = (t == n) }
    intask && /^## / { intask = 0 }
    intask && /^- \[ \] / { sub(/^- \[ \]/, "- [x]") }
    { print }
  ' "$plan" > "$TMP/plan.md"
  cat "$TMP/plan.md" > "$plan"
  title=$(printf '%s' "$line" | cut -f3)
  reqs=$(printf '%s' "$line" | cut -f4)
  commits="-"; tests="-"
  ws=$(find_workspace "$plan")
  if [ -n "$ws" ] && [ -f "$ws/progress.md" ]; then
    l=$(grep "^Task $n: complete (commits " "$ws/progress.md" | tail -n 1 || true)
    if [ -n "$l" ]; then
      commits=$(printf '%s' "$l" | sed 's/^Task [0-9]*: complete (commits \([^,]*\), tests: .*$/\1/')
      tests=$(printf '%s' "$l" | sed 's/^Task [0-9]*: complete (commits [^,]*, tests: \(.*\))$/\1/' | sed 's/|/\\|/g')
    fi
  fi
  [ "$commits" != "-" ] || echo "progress.sh: no ledger line for Task $n in $plan — recorded without test evidence" >&2
  if [ ! -f "$rep" ]; then
    mkdir -p "$TEST_DIR"
    # shellcheck disable=SC2016  # backticks are Markdown, not command substitution
    printf '# 시험결과서: %s\n\n<!-- progress.sh done 이 행을 추가한다. 직접 편집하지 않는다. -->\n\n계획서: `%s`\n설계서: `%s`\n\n| 일자 | 태스크 | 제목 | 요구사항 | 커밋 범위 | 테스트 명령 → 결과 |\n|---|---|---|---|---|---|\n' \
      "$(plan_title "$plan")" "$plan" "$(header_field "$plan" Spec)" > "$rep"
  fi
  printf '| %s | Task %s | %s | %s | %s | %s |\n' "$TODAY" "$n" "$title" "${reqs:--}" "$commits" "$tests" >> "$rep"
  echo "recorded Task $n in $rep"
  cmd_status
}

cmd_report() {
  local out="$REPORT_DIR/$TODAY-report.md" prev
  cmd_status >/dev/null
  prev=$( { git show "HEAD:$STATUS_FILE" 2>/dev/null || true; } | sed -n 's/.*"overall": {"progress": \([0-9.nul]*\),.*/\1/p' | head -n 1)
  no_symlink "$out"
  mkdir -p "$REPORT_DIR"
  PREV="$prev" run_awk "$AWK_REPORT" > "$out"
  echo "wrote $out"
}

# fill_empty <tsv> : print the file with empty fields replaced by "-", because bash
# `read` with IFS=tab collapses consecutive tabs and would shift the columns.
fill_empty() {
  awk -F'\t' -v OFS='\t' '{ for (i = 1; i <= NF; i++) if ($i == "") $i = "-"; print }' "$1"
}

# drift_view <status.json> : the part of status.json that "stale" is judged on — only what
# plans and requirements determine (overall, plans, requirements). Metadata that changes
# without any plan/requirement edit is dropped so the CI gate does not fail on a worktree or
# clone directory name (project), a standard update (standard_version), a baseline or CR
# bookkeeping edit (requirements_baseline, changes), the date, or new Refs: commits.
drift_view() {
  grep -v -e '"project"' -e '"generated_at"' -e '"standard_version"' -e '"requirements_baseline"' -e '"changes"' "$1" \
    | sed 's/"commits": [0-9]*/"commits": -/g'
}

# baseline : snapshot REQ_FILE into BASELINE_FILE (spec §5.1, CR-002), note it in REQ_FILE's
# header, then refresh status.json/traceability.md. The commit sha is informational only.
cmd_baseline() {
  local sha line
  [ -f "$REQ_FILE" ] || die "no $REQ_FILE"
  no_symlink "$REQ_FILE"; no_symlink "$BASELINE_FILE"
  sha=$(git rev-parse --short=7 HEAD 2>/dev/null || echo "-")
  {
    printf '<!-- 기준선 생성: %s (commit %s). progress.sh baseline 이 REQUIREMENTS.md 를 복사한 스냅샷. 직접 편집하지 않는다. -->\n' "$TODAY" "$sha"
    grep -v '^\*\*기준선:\*\*' "$REQ_FILE"
  } > "$TMP/base.md"
  cat "$TMP/base.md" > "$BASELINE_FILE"
  line="**기준선:** $TODAY (docs/requirements/BASELINE.md, commit $sha)"
  if grep -q '^\*\*기준선:\*\*' "$REQ_FILE"; then
    awk -v line="$line" '/^\*\*기준선:\*\*/ { print line; next } { print }' "$REQ_FILE" > "$TMP/req.md"
  else
    # before the requirements table; at the end when there is no table yet
    awk -v line="$line" '
      !done && /^\| *ID *\|/ { print line; print ""; done = 1 }
      { print }
      END { if (!done) { print ""; print line } }' "$REQ_FILE" > "$TMP/req.md"
  fi
  cat "$TMP/req.md" > "$REQ_FILE"
  echo "wrote $BASELINE_FILE and $REQ_FILE (기준선 $TODAY)"
  cmd_status
}

# check [--ci] : print every warning; exit 1 on any warning (default) or only on
# gate warnings (--ci, spec §4.1). Gate = untagged task, undefined requirement,
# unapproved spec/plan, missing/stale status.json, ledger-complete-but-open task,
# unreadable baseline, requirement changed without an approved CR.
# Non-gate = requirement with no spec/plan yet (backlog), task without checkboxes,
# proposed CR, no baseline.
cmd_check() {
  local mode=${1:-} n=0 g=0 p i st done_ ws f appr l id cr approved
  collect
  # warn <gate:1|0> <message>. Warnings quote repository content (titles, paths): strip
  # control characters so a hostile checkout cannot inject terminal escape sequences.
  warn() {
    [ "$1" = 1 ] && g=$((g + 1)); shift
    printf '%s\n' "$*" | LC_ALL=C tr -d '\000-\010\013-\037\177'; n=$((n + 1))
  }
  while IFS=$'\t' read -r p i _ _ _ st _ done_; do
    ws=$(find_workspace "$p")
    if [ "$done_" = 0 ] && [ -n "$ws" ] && [ -f "$ws/progress.md" ] && grep -q "^Task $i: complete" "$ws/progress.md"; then
      warn 1 "ledger says complete but checkboxes are open: $p Task $i (run: progress.sh done $p $i)"
    fi
    if [ "$st" = 0 ]; then warn 0 "task has no checkboxes: $p Task $i"; fi
  done < <(fill_empty "$TMP/tasks.tsv")
  while IFS= read -r l; do
    case "$l" in '설계서·계획서가 없는 요구사항'*) warn 0 "$l" ;; *) warn 1 "$l" ;; esac
  done < <(run_awk "$AWK_WARN")
  while IFS=$'\t' read -r f _ _ appr; do case "$appr" in -|'(미승인'*) warn 1 "not approved: $f" ;; esac; done < <(fill_empty "$TMP/plans.tsv")
  while IFS=$'\t' read -r f _ appr; do case "$appr" in -|'(미승인'*) warn 1 "not approved: $f" ;; esac; done < <(fill_empty "$TMP/specs.tsv")
  # requirement changes vs baseline (spec §5.1, §5.3)
  if [ -f "$REQ_FILE" ]; then
    if [ ! -f "$BASELINE_FILE" ]; then
      if [ -e "$TMP/baseline_legacy" ]; then
        warn 0 "요구사항 기준선이 구 형식(commit 참조)입니다 — progress.sh baseline 을 실행해 $BASELINE_FILE 스냅샷으로 전환"
      else
        warn 0 "요구사항 기준선 없음 (progress.sh baseline)"
      fi
    else
      approved=$(awk -F'\t' '$7 == "승인" { print $5 }' "$TMP/changes.tsv" | tr ',' '\n' | sort -u)
      while IFS=$'\t' read -r id _; do
        [ -n "$id" ] || continue
        printf '%s\n' "$approved" | grep -qxF "$id" || warn 1 "기준선 이후 변경된 요구사항에 승인된 변경요청이 없음: $id"
      done < "$TMP/reqdiff.tsv"
    fi
  fi
  # CLAUDE.md must keep importing the standard (gate); the Documents section is advisory.
  # The standard repository itself imports "@BASELINE.md" from its root.
  if [ ! -f CLAUDE.md ]; then
    warn 1 "CLAUDE.md 없음 (install.sh가 template으로 만든다)"
  else
    grep -q '^@\(\.claude/shared/\)\{0,1\}BASELINE\.md[[:space:]]*$' CLAUDE.md \
      || warn 1 "CLAUDE.md가 BASELINE을 import하지 않음 (첫 줄에 @.claude/shared/BASELINE.md)"
    grep -q '^## Documents' CLAUDE.md \
      || warn 0 "CLAUDE.md에 Documents 절 없음 (template/CLAUDE.md의 절을 추가)"
  fi
  # proposed CRs are reported even before REQUIREMENTS.md exists
  while IFS=$'\t' read -r cr _ _ _ _ _ st _; do
    [ "$st" = "제안" ] && warn 0 "미승인 변경요청: $cr"
  done < <(fill_empty "$TMP/changes.tsv")
  if [ -s "$TMP/tasks.tsv" ]; then
    run_awk "$AWK_JSON" > "$TMP/status.json"
    if [ ! -f "$STATUS_FILE" ]; then
      warn 1 "$STATUS_FILE missing (run: progress.sh status)"
    elif ! cmp -s <(drift_view "$TMP/status.json") <(drift_view "$STATUS_FILE"); then
      warn 1 "$STATUS_FILE is stale (run: progress.sh status)"
    fi
  fi
  if [ "$n" -eq 0 ]; then echo "progress.sh check: OK" >&2; return 0; fi
  if [ "$mode" = "--ci" ]; then
    [ "$g" -eq 0 ] && { echo "progress.sh check --ci: OK ($n non-gate warning(s))" >&2; return 0; }
    echo "progress.sh check --ci: $g gate warning(s)" >&2
  fi
  return 1
}

case "${1:-}" in
  done) [ $# -eq 3 ] || die "$USAGE"; cmd_done "$2" "$3" ;;
  report) [ $# -eq 1 ] || die "$USAGE"; cmd_report ;;
  check) case "${2:-}" in ''|--ci) [ $# -le 2 ] || die "$USAGE"; cmd_check "${2:-}" ;; *) die "$USAGE" ;; esac ;;
  baseline) [ $# -eq 1 ] || die "$USAGE"; cmd_baseline ;;
  status) case "${2:-}" in ''|--tsv) [ $# -le 2 ] || die "$USAGE"; cmd_status "${2:-}" ;; *) die "$USAGE" ;; esac ;;
  *) die "$USAGE" ;;
esac
