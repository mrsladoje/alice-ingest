#!/usr/bin/env bash
set -euo pipefail

LXPLUS="${LXPLUS:-lxplus.cern.ch}"
REMOTE_DIR="${REMOTE_DIR:-alice-ingest/deploy}"
AWS_PROFILE_NAME="${AWS_PROFILE_NAME:-cern_s3}"
CREDS="${CREDS:-$HOME/.aws/credentials}"

SSH_OPTS=(-o GSSAPIAuthentication=no -o PubkeyAuthentication=no)

if [ ! -f "$CREDS" ]; then
  echo "ERROR: $CREDS not found" >&2
  exit 1
fi

read -r -s -p "New Dashboards password (user 'alice'): " DASH_PW; echo
read -r -s -p "Repeat it: " DASH_PW2; echo
[ "$DASH_PW" = "$DASH_PW2" ] || { echo "ERROR: passwords do not match" >&2; exit 1; }
[ -n "$DASH_PW" ] || { echo "ERROR: empty password" >&2; exit 1; }

read -r -s -p "New vault password (this is what 'make deploy' asks for): " VPW; echo
read -r -s -p "Repeat it: " VPW2; echo
[ "$VPW" = "$VPW2" ] || { echo "ERROR: vault passwords do not match" >&2; exit 1; }
[ -n "$VPW" ] || { echo "ERROR: empty vault password" >&2; exit 1; }

B64BODY=$(DASH_PW="$DASH_PW" CREDS="$CREDS" PROFILE="$AWS_PROFILE_NAME" python3 - <<'PY'
import base64, configparser, json, os, sys

cfg = configparser.ConfigParser()
cfg.read(os.environ["CREDS"])
profile = os.environ["PROFILE"]
if profile not in cfg:
    sys.exit(f"ERROR: no [{profile}] section in {os.environ['CREDS']}")
sec = cfg[profile]
key = sec.get("aws_access_key_id", "").strip()
secret = sec.get("aws_secret_access_key", "").strip()
if not key or not secret:
    sys.exit(f"ERROR: [{profile}] is missing an id or secret")

body = (f"vault_s3_access_key_id: {json.dumps(key)}\n"
        f"vault_s3_secret_access_key: {json.dumps(secret)}\n"
        "vault_dashboards_basic_auth_password: "
        f"{json.dumps(os.environ['DASH_PW'])}\n")
print(base64.b64encode(body.encode()).decode())
PY
)

B64VPW=$(printf '%s' "$VPW" | base64 | tr -d '\n')

echo "Sending. You will be asked for your lxplus password once."

ssh "${SSH_OPTS[@]}" "$LXPLUS" "bash -s $REMOTE_DIR $B64BODY $B64VPW" <<'REMOTE'
set -eu
umask 077
cd "$HOME/$1"

VAULT=""
for c in "$HOME/alice-ingest/.venv/bin/ansible-vault" \
         "$HOME/ansible-venv/bin/ansible-vault" \
         "$(command -v ansible-vault 2>/dev/null || true)"; do
  if [ -n "$c" ] && [ -x "$c" ]; then VAULT="$c"; break; fi
done
if [ -z "$VAULT" ]; then
  echo "ERROR: no ansible-vault found on lxplus" >&2
  exit 1
fi

PW=$(mktemp)
trap 'rm -f "$PW"' EXIT
printf '%s' "$3" | base64 -d > "$PW"

printf '%s' "$2" | base64 -d > group_vars/vault.yml
chmod 600 group_vars/vault.yml
"$VAULT" encrypt --vault-password-file "$PW" group_vars/vault.yml >/dev/null

head -c 30 group_vars/vault.yml; echo
echo "encrypted with $VAULT"
REMOTE

echo
echo "Done. Now on lxplus:"
echo "  cd ~/alice-ingest && make deploy && make replay"
