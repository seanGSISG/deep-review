#!/usr/bin/env bash
# Run the deep review locally against any PR (open or merged) WITHOUT posting to GitHub.
# Used for bake-offs and prompt tuning. Findings land in <out>/<owner>-<repo>-<pr>/.
#   scripts/run_local.sh <owner/repo> <pr-number> [out-dir] [model]
# Needs: gh (authed), uv, opencode (npx fallback), ZHIPU_API_KEY (or Z_AI_API_KEY).
set -euo pipefail
REPO=$1; PR=$2; OUT=${3:-./bakeoff}; MODEL=${4:-zai-coding-plan/glm-5.3}
TOOLS=$(cd "$(dirname "$0")/.." && pwd)
export ZHIPU_API_KEY=${ZHIPU_API_KEY:-${Z_AI_API_KEY:?set ZHIPU_API_KEY}}
SLUG=$(echo "$REPO-$PR" | tr '/' '-'); WORK=$(mktemp -d); RES="$OUT/$SLUG"; mkdir -p "$RES"

read -r HEAD BASE < <(gh pr view "$PR" -R "$REPO" --json headRefOid,baseRefOid -q '"\(.headRefOid) \(.baseRefOid)"')
gh repo clone "$REPO" "$WORK/repo" -- -q
cd "$WORK/repo"
git fetch -q origin "$HEAD" "$BASE" 2>/dev/null || git fetch -q origin "pull/$PR/head"
git checkout -q "$HEAD"
mkdir -p .deep-review/signal
echo '.deep-review/' >> .git/info/exclude  # untracked .deep-review/ hangs opencode at startup
git diff "$BASE...HEAD" > .deep-review/diff.patch
gh pr view "$PR" -R "$REPO" --json title,body -q '"# \(.title)\n\n\(.body)"' > .deep-review/pr.md
sed "s/{{BASE_SHA}}/$BASE/g" "$TOOLS/prompts/review.md" > .deep-review/prompt.md
echo "[$SLUG] head=${HEAD:0:8} base=${BASE:0:8} diff=$(wc -l < .deep-review/diff.patch) lines"

STEP_TIMEOUT=${STEP_TIMEOUT:-300} bash "$TOOLS/scripts/collect_signal.sh" >/dev/null 2>&1 || true
OC=opencode; command -v opencode >/dev/null || OC="npx -y opencode-ai@1.18.31"
start=$(date +%s)
timeout "${AGENT_TIMEOUT:-1200}" $OC run --format json --pure --dangerously-skip-permissions -m "$MODEL" \
  --title "deep-review $SLUG" "$(cat .deep-review/prompt.md)" > .deep-review/agent-events.jsonl 2> .deep-review/agent.err || echo "[$SLUG] agent exit $?"
echo "[$SLUG] agent took $(( $(date +%s) - start ))s"
cp -r .deep-review/. "$RES/"
python3 - "$RES/agent-events.jsonl" "$RES/findings.json" <<'PY'
import json, sys, collections
tok = collections.Counter(); tools = collections.Counter()
for line in open(sys.argv[1]):
    try: e = json.loads(line)
    except Exception: continue
    p = e.get("part") or {}
    if p.get("type") == "step-finish":
        t = p.get("tokens", {}); tok["input"] += t.get("input", 0); tok["output"] += t.get("output", 0)
        tok["reasoning"] += t.get("reasoning", 0); tok["cache_read"] += t.get("cache", {}).get("read", 0); tok["steps"] += 1
    if p.get("type") == "tool": tools[p.get("tool")] += 1
try: n = len(json.load(open(sys.argv[2])).get("findings", []))
except Exception: n = "none"
print(f"steps={tok['steps']} tools={dict(tools)} tokens in={tok['input']} cached={tok['cache_read']} out={tok['output']} reasoning={tok['reasoning']} findings={n}")
PY
rm -rf "$WORK"
