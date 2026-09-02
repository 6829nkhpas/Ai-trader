#!/usr/bin/env python3
"""
Zerodha Kite Connect daily token refresh — end to end.

Kite access tokens expire at 06:00 IST every day and there is no way around the
login: Zerodha's own docs describe the expiry as a "regulatory requirement", and
`refresh_token` is "only available to certain approved platforms" (it comes back
empty for an ordinary app, which is why nothing here reads one). SEBI mandates a
fresh 2FA authentication each day.

So the login stays manual — everything after it does not. This script now does the
whole chain that used to be hand-carried:

    1.  open the Kite login page                       (you: log in + 2FA)
    2.  you paste the redirect URL back                (you: one paste)
    3.  exchange request_token -> access_token
    4.  update the LOCAL .env
    5.  update the SERVER .env over SSH
    6.  restart the two services that consume the token
    7.  verify the feed actually came back up

Steps 4-7 were previously done by hand (copy the token out of this script's
output, SSH in, `sed` the .env, `docker compose up`, curl a quote to check).

Usage:
    python scripts/generate_kite_tokens.py                # everything
    python scripts/generate_kite_tokens.py --no-deploy    # local .env only
    python scripts/generate_kite_tokens.py --token XXXX   # skip the browser step
    python scripts/generate_kite_tokens.py --deploy-only  # push the local token to the server

`--deploy-only` is the recovery path for a run that exchanged a token and wrote it
locally but failed before or during the deploy. Without it, recovery would mean a
whole fresh login: a request_token is single-use and already spent by then, so
there is no way to re-derive the access token that is sitting in the local .env.

Server target (defaults match the GCP Compute Engine VM, override if needed):
    --host / KITE_DEPLOY_HOST          stratai@8.234.73.219
    --ssh-key / KITE_DEPLOY_SSH_KEY    keys/stratai_gcp
    --remote-path / KITE_DEPLOY_PATH   /opt/stratai/Ai-trader
    --compose-files / KITE_DEPLOY_COMPOSE_FILES
                                       -f docker-compose.prod.yml -f docker-compose.8gb.yml
"""

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import urllib.parse
import urllib.request
import webbrowser
from urllib.error import HTTPError, URLError

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# The GCP Compute Engine VM that replaced the DigitalOcean droplet.
#
# The IP is the RESERVED static address from infra/gcp (google_compute_address),
# not an ephemeral one, so it survives a stop/start and a resize. It is used in
# preference to `app-api.stratai.live` deliberately: this script has to work at
# 06:00 every morning, and pinning the address keeps DNS out of the path for the
# SSH hop. (The verification step further down still goes through the hostname,
# because that is the path real clients take.)
#
# `stratai`, not `root`: GCP's Ubuntu images ship PermitRootLogin without-password,
# so the stack runs as a non-root user in /opt/stratai. That user is in the docker
# group, so no sudo is needed for the compose calls below.
DEFAULT_HOST = os.environ.get("KITE_DEPLOY_HOST", "stratai@8.234.73.219")
DEFAULT_SSH_KEY = os.environ.get("KITE_DEPLOY_SSH_KEY", os.path.join("keys", "stratai_gcp"))
DEFAULT_REMOTE_PATH = os.environ.get("KITE_DEPLOY_PATH", "/opt/stratai/Ai-trader")

# BOTH compose files, matching redeploy.sh's default.
#
# This is not cosmetic. docker-compose.8gb.yml carries the memory limits and
# tuning for a 4 vCPU / 8 GB host; recreating `ingestion` and `aggregator` with
# only the prod file would bring them back up UNCAPPED on a box with 8 GB total,
# which is how a token rotation turns into an OOM kill of the whole stack.
DEFAULT_COMPOSE_FILES = os.environ.get(
    "KITE_DEPLOY_COMPOSE_FILES", "-f docker-compose.prod.yml -f docker-compose.8gb.yml"
)

# The services that actually read KITE_ACCESS_TOKEN.
#
#   ingestion  — holds the upstream Kite WebSocket (the thing that dies at 06:00)
#   aggregator — caches the token in KiteApiState::new(), so it serves the REST
#                proxy (quotes, historical, instruments) with whatever it had at
#                start-up
#
# Nothing else needs bouncing. `.env` is injected via compose `env_file:` and is
# never volume-mounted, so a container only sees a new token by being recreated —
# writing the file alone looks like it worked and changes nothing.
TOKEN_CONSUMERS = ("aggregator", "ingestion")

# Kite request/access tokens are alphanumeric. Checked before either value is
# interpolated into the remote script, which is what makes that interpolation
# safe — a token containing a quote or a shell metacharacter is refused here
# rather than executed there.
TOKEN_RE = re.compile(r"^[A-Za-z0-9]+$")


def mask(secret: str) -> str:
    """`B0ZW…MCQU` — enough to correlate against a log, useless if seen."""
    if not secret:
        return "(empty)"
    if len(secret) <= 8:
        return "*" * len(secret)
    return f"{secret[:4]}…{secret[-4:]}"


# ── Local .env ───────────────────────────────────────────────────────────────


def read_env(path: str) -> dict:
    """Parse a dotenv file into a dict. Missing file yields an empty dict."""
    values = {}
    if not os.path.exists(path):
        return values
    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            values[key.strip()] = val.strip().strip('"').strip("'")
    return values


def set_env_keys(path: str, updates: dict) -> bool:
    """
    Replace the given keys in a dotenv file in place, preserving everything else.

    Exact key match on the left of the first `=`, so a value is never treated as a
    pattern. Written to a temp file and moved into place, so an interrupted run
    cannot leave a half-written .env — which for this file would take the whole
    stack down.
    """
    if not os.path.exists(path):
        print(f"[!] .env not found at {path}; skipping local update.")
        return False

    with open(path, "r", encoding="utf-8") as handle:
        lines = handle.readlines()

    seen = set()
    out = []
    for line in lines:
        key = line.split("=", 1)[0].strip()
        if key in updates:
            out.append(f"{key}={updates[key]}\n")
            seen.add(key)
        else:
            out.append(line)
    for key, val in updates.items():
        if key not in seen:
            if out and not out[-1].endswith("\n"):
                out.append("\n")
            out.append(f"{key}={val}\n")

    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        handle.writelines(out)
    os.replace(tmp, path)
    return True


# ── Kite token exchange ──────────────────────────────────────────────────────


def exchange_token(api_key: str, api_secret: str, request_token: str) -> dict:
    """POST /session/token. Returns the `data` object, or raises RuntimeError."""
    checksum = hashlib.sha256(
        f"{api_key}{request_token}{api_secret}".encode("utf-8")
    ).hexdigest()

    body = urllib.parse.urlencode(
        {"api_key": api_key, "request_token": request_token, "checksum": checksum}
    ).encode("utf-8")

    req = urllib.request.Request("https://api.kite.trade/session/token", data=body, method="POST")
    req.add_header("X-Kite-Version", "3")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")

    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")
        try:
            detail = json.loads(detail).get("message", detail)
        except Exception:
            pass
        raise RuntimeError(f"HTTP {exc.code} from Kite: {detail}") from exc
    except URLError as exc:
        raise RuntimeError(f"Network error talking to Kite: {exc.reason}") from exc

    if payload.get("status") != "success":
        raise RuntimeError(payload.get("message", "unknown Kite API error"))
    return payload.get("data", {})


# ── Server deploy ────────────────────────────────────────────────────────────


def build_remote_script(remote_path: str, access_token: str, compose_files: str) -> str:
    """
    The bash run on the VM: update .env, recreate the consumers, verify.

    Delivered over SSH's stdin (`bash -s`) rather than as an argument, so the
    token never appears in the remote argv and therefore never in a process
    listing. `set -euo pipefail` means a failed .env write cannot fall through to
    a restart that would come up with the old token.
    """
    services = " ".join(TOKEN_CONSUMERS)
    return f"""
set -euo pipefail
cd {remote_path}

TOKEN='{access_token}'

# ── 1. Update .env ─────────────────────────────────────────────────────────
# An exact-key rewrite via python rather than `sed s/.../.../`: sed would treat
# the value as a replacement expression, so a token containing `/` or `&` would
# corrupt the line. Temp file + os.replace so an interrupted write cannot leave a
# truncated .env and take the stack down.
#
# KITE_REQUEST_TOKEN is deliberately BLANKED, not stored. It is single-use and
# dead within minutes of the exchange, and docs/compliance/SECRET_ROTATION_RUNBOOK.md
# §3.3 says it should never be persisted. Nothing reads it while
# KITE_ACCESS_TOKEN is set.
python3 - "$TOKEN" <<'PY'
import os, sys
token = sys.argv[1]
path = ".env"
updates = {{"KITE_ACCESS_TOKEN": token, "KITE_REQUEST_TOKEN": ""}}
with open(path, encoding="utf-8") as fh:
    lines = fh.readlines()
seen = set()
out = []
for line in lines:
    key = line.split("=", 1)[0].strip()
    if key in updates:
        out.append(f"{{key}}={{updates[key]}}\\n")
        seen.add(key)
    else:
        out.append(line)
for key, val in updates.items():
    if key not in seen:
        if out and not out[-1].endswith("\\n"):
            out.append("\\n")
        out.append(f"{{key}}={{val}}\\n")
tmp = path + ".tmp"
with open(tmp, "w", encoding="utf-8") as fh:
    fh.writelines(out)
# Carry the original permissions across. .env is 0600 on the server and holds the
# broker secrets; a fresh temp file is created at the umask default (0644), so
# without this a token rotation would quietly widen it to world-readable on a box
# that has other user accounts on it.
os.chmod(tmp, os.stat(path).st_mode & 0o777)
os.replace(tmp, path)
print("[server] .env updated")
PY

# Confirm the value actually landed before restarting anything.
grep -q "^KITE_ACCESS_TOKEN=$TOKEN$" .env || {{ echo "[server] .env write did not take"; exit 1; }}

# ── 2. Recreate only the token consumers ──────────────────────────────────
# `--no-deps` is the important flag. Without it, compose also recreates
# questdb and redpanda because the consumers depend_on them — bouncing the
# database and the broker to rotate a token, and briefly taking every other
# service's upstream with them.
echo "[server] recreating: {services}"
docker compose {compose_files} up -d --force-recreate --no-deps {services}

# ── 3. Verify ─────────────────────────────────────────────────────────────
# Two independent signals, because they fail differently:
#   quote      — the aggregator's REST path accepted the token
#   ws gauge   — ingestion actually authenticated the WebSocket, which is what
#                feeds everything downstream. A stale token leaves every other
#                health check green while the whole platform serves nothing.
set -a; . ./.env; set +a

echo "[server] verifying (up to 60s for the WS handshake)…"
QUOTE_OK=0
WS_OK=0
for attempt in $(seq 1 12); do
  sleep 5
  if [ "$QUOTE_OK" -eq 0 ]; then
    if curl -sf --max-time 10 -u "$QUESTDB_USER:$QUESTDB_PASSWORD" \\
        "https://app-api.stratai.live/kite/quote?i=NSE:RELIANCE" -o /tmp/kite_quote.json 2>/dev/null; then
      if python3 -c "import json,sys; d=json.load(open('/tmp/kite_quote.json')); q=d['quotes'][0]; sys.exit(0 if q.get('last_price',0)>0 else 1)" 2>/dev/null; then
        QUOTE_OK=1
        python3 -c "import json; d=json.load(open('/tmp/kite_quote.json')); q=d['quotes'][0]; print('[server] quote OK  ', q['symbol'], q['last_price'])"
      fi
    fi
  fi
  if [ "$WS_OK" -eq 0 ]; then
    VAL=$(curl -sf --max-time 10 -u "$QUESTDB_USER:$QUESTDB_PASSWORD" \\
      "https://app-api.stratai.live/prometheus/api/v1/query?query=ingestion_kite_ws_connected" 2>/dev/null \\
      | python3 -c "import json,sys; r=json.load(sys.stdin)['data']['result']; print(r[0]['value'][1] if r else '')" 2>/dev/null || true)
    if [ "$VAL" = "1" ]; then
      WS_OK=1
      echo "[server] kite WS  OK   ingestion_kite_ws_connected=1"
    fi
  fi
  [ "$QUOTE_OK" -eq 1 ] && [ "$WS_OK" -eq 1 ] && break
done

rm -f /tmp/kite_quote.json
[ "$QUOTE_OK" -eq 1 ] || echo "[server] WARN quote check did not pass"
[ "$WS_OK" -eq 1 ]    || echo "[server] WARN ingestion_kite_ws_connected is not 1"
if [ "$QUOTE_OK" -eq 1 ] && [ "$WS_OK" -eq 1 ]; then
  echo "[server] VERIFIED"
else
  exit 2
fi
"""


def deploy(host: str, ssh_key: str, remote_path: str, access_token: str, compose_files: str) -> bool:
    """Push the token to the VM, restart the consumers, verify. True on success."""
    key_path = ssh_key if os.path.isabs(ssh_key) else os.path.join(PROJECT_ROOT, ssh_key)
    if not os.path.exists(key_path):
        print(f"[!] SSH key not found at {key_path}")
        return False

    cmd = [
        "ssh",
        "-i", key_path,
        # `accept-new`, not `no`: pins the host key on first contact and verifies
        # it afterwards. `no` accepts a changed key silently, which is the one
        # case worth noticing when a broker token is about to cross the link.
        "-o", "StrictHostKeyChecking=accept-new",
        "-o", "ConnectTimeout=20",
        host,
        "bash -s",
    ]

    print(f"[*] Deploying to {host}:{remote_path} …")
    try:
        # Encoded here and sent as BYTES rather than handed to subprocess as text.
        # A shell script is bytes, and text mode mangled it two independent ways on
        # a Windows host, each of which broke the deploy after the local .env had
        # already been rotated — so the token was live locally while the droplet
        # kept serving the expired one:
        #
        #   · the pipe was encoded with `locale.getpreferredencoding()` (cp1252),
        #     which cannot represent the `─`, `…` and `§` in the script's own
        #     comments — UnicodeEncodeError in subprocess's writer thread.
        #   · text mode then translates `\n` to `os.linesep`, so every line
        #     arrived with a trailing `\r`. bash read `set -euo pipefail\r` as an
        #     invalid option name and refused the script outright.
        #
        # A binary pipe does neither. Output is decoded below with `replace`, so a
        # stray byte from the remote cannot fail a rotation that otherwise worked.
        result = subprocess.run(
            cmd,
            input=build_remote_script(remote_path, access_token, compose_files).encode("utf-8"),
            capture_output=True,
            timeout=420,
        )
    except FileNotFoundError:
        print("[!] `ssh` not found on PATH.")
        return False
    except subprocess.TimeoutExpired:
        print("[!] Deploy timed out.")
        return False

    for line in result.stdout.decode("utf-8", "replace").splitlines():
        print(f"    {line}")
    if result.returncode != 0:
        for line in result.stderr.decode("utf-8", "replace").splitlines()[-15:]:
            print(f"    ! {line}")
        print(f"[!] Deploy failed (exit {result.returncode}).")
        return False
    return True


# ── Entry point ──────────────────────────────────────────────────────────────


def resolve_request_token(api_key: str, supplied: str) -> str:
    """Get a request_token: use the supplied one, else drive the login flow."""
    if supplied:
        raw = supplied
    else:
        login_url = f"https://kite.zerodha.com/connect/login?v=3&api_key={api_key}"
        print("\n── Step 1: authenticate with Zerodha ────────────────────────")
        print("This step cannot be automated: the daily expiry is a regulatory")
        print("2FA requirement, not a technical one.\n")
        print(f"  {login_url}\n")
        try:
            webbrowser.open(login_url)
            print("[*] Opened your browser.")
        except Exception:
            print("[!] Could not open a browser — use the URL above.")

        print("\n── Step 2: paste the redirect URL ───────────────────────────")
        print("After logging in you land on your redirect URL, which carries")
        print("?request_token=… — paste the whole thing (or just the token).")
        raw = input("\n> ").strip()

    if not raw:
        print("[!] No request token provided.")
        sys.exit(1)

    match = re.search(r"request_token=([A-Za-z0-9]+)", raw)
    token = match.group(1) if match else raw
    if not TOKEN_RE.match(token):
        print(f"[!] '{token}' is not a valid request token (expected alphanumeric).")
        sys.exit(1)
    return token


def obtain_access_token(env: dict, env_path: str, supplied_token: str) -> str:
    """Steps 1-4: log in, exchange the request token, store it in the local .env."""
    api_key = env.get("KITE_API_KEY", "")
    api_secret = env.get("KITE_API_SECRET", "")

    if api_key:
        print(f"[+] KITE_API_KEY    {api_key}")
    else:
        api_key = input("KITE_API_KEY: ").strip()
    if api_secret:
        print("[+] KITE_API_SECRET [masked]")
    else:
        api_secret = input("KITE_API_SECRET: ").strip()
    if not api_key or not api_secret:
        print("[!] Both KITE_API_KEY and KITE_API_SECRET are required.")
        sys.exit(1)

    request_token = resolve_request_token(api_key, supplied_token)

    print("\n── Step 3: exchange for an access token ─────────────────────")
    try:
        data = exchange_token(api_key, api_secret, request_token)
    except RuntimeError as exc:
        print(f"[!] {exc}")
        sys.exit(1)

    access_token = data.get("access_token", "")
    if not TOKEN_RE.match(access_token or ""):
        print("[!] Kite returned an access token in an unexpected format; refusing to deploy it.")
        sys.exit(1)

    print(f"[+] Authenticated as {data.get('user_name', '?')} ({data.get('user_id', '?')})")
    # Masked on purpose. The old version printed it in full, which is how tokens
    # ended up copy-pasted into chat logs and terminal scrollback; nothing
    # downstream needs a human to read it now.
    print(f"[+] access_token    {mask(access_token)}  (valid until 06:00 IST tomorrow)")

    # Stored BEFORE the deploy, deliberately: if the deploy then fails, the token
    # is still on disk and `--deploy-only` can finish the job without a new login.
    print("\n── Step 4: update the local .env ────────────────────────────")
    if set_env_keys(env_path, {"KITE_ACCESS_TOKEN": access_token, "KITE_REQUEST_TOKEN": ""}):
        print(f"[+] {env_path}")
        print("    KITE_REQUEST_TOKEN blanked — single-use, dead within minutes,")
        print("    and SECRET_ROTATION_RUNBOOK.md §3.3 says not to persist it.")

    return access_token


def reuse_local_access_token(env: dict, env_path: str) -> str:
    """The `--deploy-only` path: take the token the local .env already holds."""
    access_token = env.get("KITE_ACCESS_TOKEN", "")
    if not TOKEN_RE.match(access_token or ""):
        print(f"[!] --deploy-only reuses KITE_ACCESS_TOKEN from {env_path},")
        print("    but there is no valid one there. Run without the flag to log in.")
        sys.exit(1)
    print(f"[+] KITE_ACCESS_TOKEN {mask(access_token)}  (reused from the local .env)")
    print("    Steps 1-4 skipped — already exchanged and stored by an earlier run.")
    return access_token


def main() -> None:
    parser = argparse.ArgumentParser(description="Refresh the Kite access token, locally and on the server.")
    parser.add_argument("--no-deploy", action="store_true", help="update the local .env only")
    parser.add_argument(
        "--deploy-only",
        action="store_true",
        help="skip the login and push the local .env's KITE_ACCESS_TOKEN to the server",
    )
    parser.add_argument("--token", default="", help="a request_token or redirect URL, to skip the browser step")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--ssh-key", default=DEFAULT_SSH_KEY)
    parser.add_argument("--remote-path", default=DEFAULT_REMOTE_PATH)
    parser.add_argument(
        "--compose-files",
        default=DEFAULT_COMPOSE_FILES,
        help="compose -f flags used for the restart; must include the 8gb override on the GCP VM",
    )
    args = parser.parse_args()

    # This output carries box-drawing rules, em dashes and `✔`. A Windows console
    # takes them directly, but a REDIRECTED stdout falls back to the locale codec
    # (cp1252), where they are unencodable — so piping this to a log file, or
    # running it from anything that captures output, killed it mid-rotation.
    # Declaring the encoding here is the difference between a tool that works in a
    # terminal and one that works when automated, which is the whole point of it.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass  # already UTF-8, or a stream that cannot be reconfigured

    print("=" * 60)
    print("  Zerodha Kite Connect — daily token refresh")
    print("=" * 60)

    if args.deploy_only and args.no_deploy:
        print("[!] --deploy-only and --no-deploy contradict each other.")
        sys.exit(1)

    env_path = os.path.join(PROJECT_ROOT, ".env")
    env = read_env(env_path)

    if args.deploy_only:
        access_token = reuse_local_access_token(env, env_path)
    else:
        access_token = obtain_access_token(env, env_path, args.token)

    if args.no_deploy:
        print("\n[*] --no-deploy: server untouched.")
        print("    Run without the flag to update the server and restart the feed.")
        return

    print("\n── Steps 5-7: server .env, restart, verify ──────────────────")
    if deploy(args.host, args.ssh_key, args.remote_path, access_token, args.compose_files):
        print("\n[✔] Done — token rotated, services recreated, feed verified live.")
    else:
        print("\n[✘] Server update did not complete. The local .env is updated;")
        print("    the server may still be running the previous token.")
        sys.exit(1)


if __name__ == "__main__":
    main()
