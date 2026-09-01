#!/usr/bin/env bash
# auth check 只证明本地有凭证；get_minimal_app_list 才验 token / 网关名。
set -euo pipefail

CONTEXT=""

usage() {
  cat <<'EOF'
Usage:
  guard.sh [--context <name>]
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --context) CONTEXT="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown arg: $1" >&2; usage; exit 2 ;;
  esac
done

if ! command -v bk-cli >/dev/null 2>&1; then
  echo "bk-cli not found in PATH" >&2
  exit 1
fi
if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 not found in PATH" >&2
  exit 1
fi

BK=(bk-cli)
if [[ -n "$CONTEXT" ]]; then
  BK+=(--context "$CONTEXT")
fi

echo "checking local credentials (auth check does not validate token)..." >&2
AUTH_JSON="$("${BK[@]}" auth check)"
python3 -c '
import json, sys
doc = json.loads(sys.argv[1])
if not doc.get("ok", False) or not (doc.get("data") or {}).get("has_credentials"):
    sys.stderr.write(sys.argv[1] + "\n")
    sys.stderr.write("no local credentials; ask the user to login\n")
    sys.exit(1)
' "$AUTH_JSON"

echo "inspecting gateway url..." >&2
DRY_JSON="$("${BK[@]}" paas get_minimal_app_list --dry-run)"
GW_URL="$(
  python3 -c '
import json, sys
doc = json.loads(sys.argv[1])
url = ((doc.get("request") or {}).get("url")) or ""
if not url:
    sys.stderr.write(sys.argv[1] + "\n")
    sys.stderr.write("dry-run missing request.url\n")
    sys.exit(1)
print(url)
' "$DRY_JSON"
)"
echo "gateway url=$GW_URL" >&2

echo "validating token with get_minimal_app_list..." >&2
set +e
LIST_JSON="$("${BK[@]}" paas get_minimal_app_list 2>&1)"
LIST_RC=$?
set -e

python3 -c '
import json, sys

raw, url, rc = sys.argv[1], sys.argv[2], int(sys.argv[3])
try:
    doc = json.loads(raw)
except json.JSONDecodeError:
    sys.stderr.write(raw + "\n")
    sys.exit(1)

if doc.get("ok", False):
    sys.exit(0)

sys.stderr.write(raw + "\n")
text = raw.lower()
headers = doc.get("headers") or {}
err_code = str(headers.get("X-Bkapi-Error-Code") or headers.get("x-bkapi-error-code") or "")
status = doc.get("status")

if "access_token" in text and "invalid" in text:
    sys.stderr.write("token invalid; auth check cannot catch this. ask the user to re-login\n")
    sys.exit(1)

all_403 = status == 403 or err_code == "1640301" or "1640301" in raw or "app_no_permission" in text
if all_403 and "bkpaas3." in url:
    sys.stderr.write(
        "all-API 403 with bkpaas3 host: binary likely missing BK_TE_DOMAIN. "
        "rebuild with scripts/install.sh; do not apply for bkpaas3 permission\n"
    )
    sys.exit(1)
if all_403:
    sys.stderr.write(
        "403 1640301: first confirm dry-run URL is paasv3 (not bkpaas3). "
        "if URL is already paasv3, then apply API permission for the caller app_code\n"
    )
    sys.exit(1)

if rc != 0:
    sys.exit(rc)
sys.exit(1)
' "$LIST_JSON" "$GW_URL" "$LIST_RC"

echo "guard ok" >&2
printf '%s\n' "$LIST_JSON"
