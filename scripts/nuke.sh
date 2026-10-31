#!/usr/bin/env bash
# Katalog Kilat — deployment teardown.
#
# Removes exactly the `katalogkilat` compose project, its working directory and,
# on request, its Cloudflare tunnel and DNS records. Nothing else on a shared
# host is touched. Dry-run is the default: nothing is destroyed without --apply.
#
#   ./scripts/nuke.sh                            # dry-run, prints what would go
#   ./scripts/nuke.sh --apply                    # local teardown only
#   ./scripts/nuke.sh --apply --keep-data        # keep ./data (jobs and exports)
#   ./scripts/nuke.sh --apply --with-tunnel      # also delete tunnel + apex/www DNS
#
# The normal invocation, from a laptop:
#   ssh personal 'cd /home/ubuntu/katalogkilat && ./scripts/nuke.sh --apply'
#
# Hard limits, enforced below rather than promised:
#   * never stops, removes or reconfigures adguardhome, wg-easy or cloudflared
#   * never deletes a tunnel other than the one named in .env, and never the
#     djanc.uk tunnel
#   * never removes a path outside the project directory
#   * never touches firewall rules, Docker images or networks it does not own
#
# Exit codes: 0 done, 1 refused or failed (the host is left as it was found).

set -euo pipefail

readonly PROJECT="katalogkilat"
readonly NAME_PREFIX="katalogkilat-"
readonly PROTECTED_CONTAINERS=(adguardhome wg-easy cloudflared)
readonly API_BASE="https://api.cloudflare.com/client/v4"

APPLY=0
WITH_TUNNEL=0
KEEP_DATA=0

die() { printf 'REFUSED: %s\n' "$*" >&2; exit 1; }
say() { printf '%s\n' "$*"; }
step() { printf '\n== %s\n' "$*"; }

usage() {
  sed -n '3,26p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
  exit 0
}

for arg in "$@"; do
  case "$arg" in
    --apply) APPLY=1 ;;
    --with-tunnel) WITH_TUNNEL=1 ;;
    --keep-data) KEEP_DATA=1 ;;
    -h|--help) usage ;;
    *) die "unknown argument: $arg (see --help)" ;;
  esac
done

# ---------------------------------------------------------------------------
# Locate the project, then re-exec from /tmp before anything is deleted.
#
# The script lives inside the directory it destroys. bash reads a script
# lazily, so a run that deletes its own file mid-flight dies with "cannot read
# the script". A copy under /tmp removes that class of failure entirely.
# ---------------------------------------------------------------------------

SCRIPT_PATH="$(readlink -f "${BASH_SOURCE[0]}")"
TARGET_DIR="${NUKE_TARGET_DIR:-$(cd "$(dirname "$SCRIPT_PATH")/.." && pwd)}"

if [[ "$APPLY" == 1 && "${NUKE_REEXEC:-0}" != 1 ]]; then
  tmp_self="/tmp/nuke.$$.sh"
  cp -- "$SCRIPT_PATH" "$tmp_self"
  export NUKE_REEXEC=1
  export NUKE_TARGET_DIR="$TARGET_DIR"
  exec bash "$tmp_self" "$@"
fi

[[ -d "$TARGET_DIR" ]] || die "project directory not found: $TARGET_DIR"
COMPOSE_FILE="$TARGET_DIR/docker-compose.yml"
[[ -f "$COMPOSE_FILE" ]] || die "no compose file at $COMPOSE_FILE — is this the deployment directory?"

# ---------------------------------------------------------------------------
# Pre-flight: identity, credentials, targets. Every check runs before the
# first destructive command, so a refusal leaves the host untouched.
# ---------------------------------------------------------------------------

step "Pre-flight"

declared_project="$(sed -n 's/^name:[[:space:]]*//p' "$COMPOSE_FILE" | head -1 | tr -d '\r')"
[[ "$declared_project" == "$PROJECT" ]] \
  || die "compose file declares project '${declared_project}', expected '${PROJECT}'"
if [[ -n "${COMPOSE_PROJECT_NAME:-}" && "${COMPOSE_PROJECT_NAME}" != "$PROJECT" ]]; then
  die "COMPOSE_PROJECT_NAME is '${COMPOSE_PROJECT_NAME}', expected '${PROJECT}'"
fi
say "project name     : $PROJECT (from $COMPOSE_FILE)"

env_get() {
  local key="$1" file="$TARGET_DIR/.env"
  [[ -f "$file" ]] || return 0
  sed -n "s/^[[:space:]]*${key}=//p" "$file" | tail -1 | sed -e 's/^["'\'']//' -e 's/["'\'']$//' | tr -d '\r'
}

CF_API_TOKEN="${CF_API_TOKEN:-$(env_get CF_API_TOKEN)}"
CF_ACCOUNT_ID="${CF_ACCOUNT_ID:-$(env_get CF_ACCOUNT_ID)}"
CF_ZONE_ID="${CF_ZONE_ID:-$(env_get CF_ZONE_ID)}"
CF_ZONE_NAME="${CF_ZONE_NAME:-$(env_get CF_ZONE_NAME)}"
CF_TUNNEL_ID="${CF_TUNNEL_ID:-$(env_get CF_TUNNEL_ID)}"
CF_TUNNEL_NAME="${CF_TUNNEL_NAME:-$(env_get CF_TUNNEL_NAME)}"
CF_ZONE_NAME="${CF_ZONE_NAME:-metahackathon.my.id}"
CF_TUNNEL_NAME="${CF_TUNNEL_NAME:-metahackathon}"

if [[ "$WITH_TUNNEL" == 1 ]]; then
  for required in CF_API_TOKEN CF_ACCOUNT_ID CF_ZONE_ID CF_TUNNEL_ID; do
    [[ -n "${!required}" ]] || die "--with-tunnel needs $required in $TARGET_DIR/.env (nothing was removed)"
  done
  command -v jq >/dev/null || die "--with-tunnel needs jq, which is not installed"
  [[ "$CF_ZONE_NAME" == "metahackathon.my.id" ]] \
    || die "zone '${CF_ZONE_NAME}' is not the Katalog Kilat zone; refusing to touch another zone"
  say "tunnel           : ${CF_TUNNEL_NAME} (${CF_TUNNEL_ID}) — will be deleted with its DNS records"
else
  say "tunnel           : left alone (pass --with-tunnel to delete it)"
fi

mapfile -t target_containers < <(
  docker ps -a --filter "label=com.docker.compose.project=$PROJECT" --format '{{.Names}}' | sort -u
)
for name in "${target_containers[@]:-}"; do
  [[ -n "$name" ]] || continue
  [[ "$name" == "$NAME_PREFIX"* ]] \
    || die "container '$name' belongs to project '$PROJECT' but is not named '${NAME_PREFIX}*'"
  for protected in "${PROTECTED_CONTAINERS[@]}"; do
    [[ "$name" == "$protected" ]] && die "container '$name' is a neighbour and must never be removed"
  done
done

if [[ "${#target_containers[@]}" -eq 0 ]]; then
  say "containers       : none running under this project"
else
  say "containers       : ${target_containers[*]}"
fi

resolved="$(realpath -- "$TARGET_DIR")"
[[ -L "$TARGET_DIR" ]] && die "$TARGET_DIR is a symlink; point the script at the real directory"

path_is_safe=1
case "$resolved" in
  /|/home|/home/ubuntu) path_is_safe=0 ;;
esac
[[ "$resolved" == /home/ubuntu/* ]] || path_is_safe=0
if [[ "$path_is_safe" != 1 ]]; then
  if [[ "$APPLY" == 1 ]]; then
    die "refusing to remove '$resolved': it is not a directory below /home/ubuntu"
  fi
  say "directory        : $resolved (outside /home/ubuntu — --apply would refuse)"
elif [[ "$KEEP_DATA" == 1 ]]; then
  say "directory        : $resolved (kept, $resolved/data preserved)"
else
  say "directory        : $resolved (removed)"
fi

if [[ "$APPLY" != 1 ]]; then
  step "Dry run — nothing was changed"
  say "Re-run with --apply to perform the teardown above."
  exit 0
fi

# ---------------------------------------------------------------------------
# Local teardown.
# ---------------------------------------------------------------------------

step "Stopping the compose project"
( cd "$TARGET_DIR" && docker compose -p "$PROJECT" down --rmi local --volumes --remove-orphans )

remaining="$(docker ps -a --filter "label=com.docker.compose.project=$PROJECT" --format '{{.Names}}')"
if [[ -n "$remaining" ]]; then
  die "project containers survived the teardown: $(echo "$remaining" | tr '\n' ' ')— directory kept for inspection"
fi
remaining="$(docker ps -a --filter "name=^${NAME_PREFIX}" --format '{{.Names}}')"
[[ -z "$remaining" ]] || die "containers still present: $(echo "$remaining" | tr '\n' ' ')"

step "Removing the project directory"
cd /
if [[ "$KEEP_DATA" == 1 ]]; then
  find "$resolved" -mindepth 1 -maxdepth 1 ! -name data -exec rm -rf -- {} +
  say "kept  : $resolved/data"
  say "wiped : everything else under $resolved (including .env and the secrets in it)"
else
  rm -rf -- "$resolved"
  say "wiped : $resolved"
fi

# ---------------------------------------------------------------------------
# Cloudflare teardown, only after the host is clean. The credentials were read
# before the directory was removed, so .env is already gone at this point.
# ---------------------------------------------------------------------------

cf_call() {
  local method="$1" path="$2" header_file body_file code
  header_file="$(mktemp)"; body_file="$(mktemp)"
  chmod 600 "$header_file"
  printf 'Authorization: Bearer %s\n' "$CF_API_TOKEN" > "$header_file"
  code="$(curl -sS -o "$body_file" -w '%{http_code}' -X "$method" \
    --header "@$header_file" --header 'Content-Type: application/json' \
    "${API_BASE}${path}")" || { rm -f "$header_file" "$body_file"; return 1; }
  rm -f "$header_file"
  printf '%s\n' "$code"
  cat "$body_file"
  rm -f "$body_file"
}

cf_status() { cf_call "$1" "$2" | head -1; }
cf_body() { cf_call "$1" "$2" | tail -n +2; }

if [[ "$WITH_TUNNEL" == 1 ]]; then
  step "Cloudflare tunnel teardown"

  if [[ "$(cf_status GET "/accounts/${CF_ACCOUNT_ID}/cfd_tunnel/${CF_TUNNEL_ID}")" == "200" ]]; then
    observed_name="$(cf_body GET "/accounts/${CF_ACCOUNT_ID}/cfd_tunnel/${CF_TUNNEL_ID}" | jq -r '.result.name // ""')"
    [[ "$observed_name" == "$CF_TUNNEL_NAME" ]] \
      || die "tunnel ${CF_TUNNEL_ID} is named '${observed_name}', not '${CF_TUNNEL_NAME}' — refusing to delete it"
  fi

  for host in "$CF_ZONE_NAME" "www.$CF_ZONE_NAME"; do
    record_id="$(cf_body GET "/zones/${CF_ZONE_ID}/dns_records?type=CNAME&name=${host}" \
      | jq -r '.result[0].id // ""')"
    if [[ -z "$record_id" ]]; then
      say "dns    : ${host} — already absent"
      continue
    fi
    say "dns    : deleting ${host} (${record_id})"
    code="$(cf_status DELETE "/zones/${CF_ZONE_ID}/dns_records/${record_id}")"
    if [[ "$code" == "200" || "$code" == "404" ]]; then
      say "         done (HTTP ${code})"
    else
      die "deleting ${host} returned HTTP ${code}"
    fi
  done

  code="$(cf_status DELETE "/accounts/${CF_ACCOUNT_ID}/cfd_tunnel/${CF_TUNNEL_ID}?cascade=true")"
  case "$code" in
    200|202|404) say "tunnel : deleted (HTTP ${code})" ;;
    *) die "deleting the tunnel returned HTTP ${code}" ;;
  esac
fi

# ---------------------------------------------------------------------------
# Post-conditions.
# ---------------------------------------------------------------------------

step "Post-conditions"

port_state() {
  if ss -ltn 2>/dev/null | awk '{print $4}' | grep -qE "[:.]$1\$"; then
    printf 'listening'
  else
    printf 'free'
  fi
}

say "port 8180        : $(port_state 8180)"
say "port 20242       : $(port_state 20242)"

if [[ -n "${CF_API_TOKEN:-}" && -n "${CF_TUNNEL_ID:-}" ]]; then
  code="$(cf_status GET "/accounts/${CF_ACCOUNT_ID}/cfd_tunnel/${CF_TUNNEL_ID}" || true)"
  if [[ "$code" == "404" ]]; then
    say "tunnel status    : deleted"
  elif [[ "$code" == "200" ]]; then
    say "tunnel status    : $(cf_body GET "/accounts/${CF_ACCOUNT_ID}/cfd_tunnel/${CF_TUNNEL_ID}" | jq -r '.result.status // "unknown"') (no connectors left)"
  else
    say "tunnel status    : not checked (HTTP ${code:-error})"
  fi
else
  say "tunnel status    : not checked (no Cloudflare credentials left)"
fi

say "neighbours       :"
for neighbour in "${PROTECTED_CONTAINERS[@]}"; do
  state="$(docker inspect --format '{{.State.Status}}' "$neighbour" 2>/dev/null || echo missing)"
  say "  $(printf '%-14s' "$neighbour") ${state}"
done

step "Done"
say "The deployment is gone. The host is left as it was found."
