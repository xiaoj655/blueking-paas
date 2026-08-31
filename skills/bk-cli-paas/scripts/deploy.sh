#!/usr/bin/env bash
# Deploy a BlueKing PaaS module via bk-cli and wait for a terminal status.
set -euo pipefail

APP_CODE=""
MODULE="default"
ENV="stag"
BRANCH="main"
TAG=""
REVISION=""
CONTEXT=""
POLL_SEC=5
TIMEOUT_SEC=900

usage() {
  cat <<'EOF'
Usage:
  deploy.sh --app-code <code> [--module default] [--env stag] \
            [--branch main | --tag <name>] [--revision <sha>] \
            [--context <name>] [--poll-sec 5] [--timeout-sec 900]
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --app-code) APP_CODE="$2"; shift 2 ;;
    --module) MODULE="$2"; shift 2 ;;
    --env) ENV="$2"; shift 2 ;;
    --branch) BRANCH="$2"; shift 2 ;;
    --tag) TAG="$2"; shift 2 ;;
    --revision) REVISION="$2"; shift 2 ;;
    --context) CONTEXT="$2"; shift 2 ;;
    --poll-sec) POLL_SEC="$2"; shift 2 ;;
    --timeout-sec) TIMEOUT_SEC="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown arg: $1" >&2; usage; exit 2 ;;
  esac
done

if [[ -z "$APP_CODE" ]]; then
  echo "--app-code is required" >&2
  usage
  exit 2
fi
if [[ -n "$TAG" ]]; then
  BRANCH=""
fi
if [[ -z "$BRANCH" && -z "$TAG" ]]; then
  echo "need --branch or --tag" >&2
  usage
  exit 2
fi

BK=(bk-cli)
if [[ -n "$CONTEXT" ]]; then
  BK+=(--context "$CONTEXT")
fi

json_query() {
  python3 -c '
import json, sys
raw = sys.stdin.read()
try:
    doc = json.loads(raw)
except json.JSONDecodeError:
    sys.stderr.write(raw + "\n")
    sys.exit(1)
if not doc.get("ok", False):
    sys.stderr.write(raw + "\n")
    sys.exit(1)
cur = doc.get("data", doc)
path = sys.argv[1]
if path:
    for key in path.split("."):
        if isinstance(cur, dict):
            cur = cur.get(key)
        else:
            cur = None
            break
if cur is None:
    sys.exit(2)
if isinstance(cur, (dict, list)):
    print(json.dumps(cur, ensure_ascii=False))
else:
    print(cur)
' "$1"
}

run_cli() {
  "${BK[@]}" "$@"
}

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GUARD_ARGS=()
if [[ -n "$CONTEXT" ]]; then
  GUARD_ARGS+=(--context "$CONTEXT")
fi
"$SCRIPT_DIR/guard.sh" "${GUARD_ARGS[@]}" >/dev/null

if [[ -z "$REVISION" ]]; then
  echo "resolving revision from repo branches..." >&2
  NAME="$BRANCH"
  WANT_TYPE="branch"
  if [[ -n "$TAG" ]]; then
    NAME="$TAG"
    WANT_TYPE="tag"
  fi
  BRANCHES_JSON="$(run_cli paas get_repo_branches --app_code "$APP_CODE" --module "$MODULE")"
  REVISION="$(
    python3 -c '
import json, sys
doc = json.loads(sys.argv[1])
if not doc.get("ok", False):
    sys.stderr.write(sys.argv[1] + "\n")
    sys.exit(1)
data = doc.get("data") or {}
results = data.get("results") if isinstance(data, dict) else data
if not isinstance(results, list):
    sys.stderr.write("unexpected get_repo_branches payload\n")
    sys.exit(1)
name, want = sys.argv[2], sys.argv[3]
for item in results:
    if item.get("name") == name and item.get("type") == want:
        rev = item.get("revision")
        if not rev:
            sys.stderr.write("branch/tag found but revision empty\n")
            sys.exit(1)
        print(rev)
        sys.exit(0)
sys.stderr.write("branch/tag not found: %s (%s)\n" % (name, want))
sys.exit(1)
' "$BRANCHES_JSON" "$NAME" "$WANT_TYPE"
  )"
fi

if [[ -n "$BRANCH" ]]; then
  VERSION_TYPE="branch"
  VERSION_NAME="$BRANCH"
else
  VERSION_TYPE="tag"
  VERSION_NAME="$TAG"
fi

BODY="$(python3 -c 'import json,sys; print(json.dumps({"revision":sys.argv[1],"version_type":sys.argv[2],"version_name":sys.argv[3],"advanced_options":{"image_pull_policy":"IfNotPresent"}}))' "$REVISION" "$VERSION_TYPE" "$VERSION_NAME")"

echo "deploying $APP_CODE module=$MODULE env=$ENV $VERSION_TYPE=$VERSION_NAME revision=$REVISION" >&2
DEPLOY_JSON="$(run_cli paas deploy_with_module --app_code "$APP_CODE" --module "$MODULE" --env "$ENV" --body "$BODY")"
DEPLOYMENT_ID="$(printf '%s' "$DEPLOY_JSON" | json_query deployment_id)"
echo "deployment_id=$DEPLOYMENT_ID" >&2

START_TS="$(date +%s)"
STATUS="pending"
RESULT_JSON=""
while true; do
  RESULT_JSON="$(run_cli paas get_deployment_result --app_code "$APP_CODE" --module "$MODULE" --deployment_id "$DEPLOYMENT_ID")"
  STATUS="$(printf '%s' "$RESULT_JSON" | json_query status || true)"
  echo "status=$STATUS" >&2
  case "$STATUS" in
    successful|failed|interrupted) break ;;
  esac
  NOW="$(date +%s)"
  if (( NOW - START_TS >= TIMEOUT_SEC )); then
    echo "timed out after ${TIMEOUT_SEC}s waiting for $DEPLOYMENT_ID" >&2
    printf '%s\n' "$RESULT_JSON"
    exit 1
  fi
  sleep "$POLL_SEC"
done

printf '%s\n' "$RESULT_JSON"

if [[ "$STATUS" == "successful" ]]; then
  STATE_JSON="$(run_cli paas module_env_released_state --code "$APP_CODE" --module_name "$MODULE" --environment "$ENV" || true)"
  printf '%s\n' "$STATE_JSON"
  exit 0
fi
exit 1
