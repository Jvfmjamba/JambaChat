#!/usr/bin/env bash
set -u
cd "$(dirname "$0")/.."
export UV_LINK_MODE="${UV_LINK_MODE:-copy}"

if command -v uv >/dev/null 2>&1; then UV=(uv); else UV=(python -m uv); fi
[[ "${1:-}" == "--fix" ]] && "${UV[@]}" run ruff format -q . && "${UV[@]}" run ruff check -q --fix .

tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT

run() {
  local name=$1; shift
  if "${UV[@]}" run "$@" >"$tmp/$name" 2>&1; then echo ok >"$tmp/$name.status"; else echo fail >"$tmp/$name.status"; fi
}

run format ruff format --check -q . &
run lint ruff check -q . &
run types mypy src tests &
run tests pytest -q -n "${PYTEST_WORKERS:-4}" -p no:cacheprovider &
wait

status=0
for name in format lint types tests; do
  if [[ $(cat "$tmp/$name.status") == ok ]]; then
    printf '%-7s ok\n' "$name"
  else
    printf '%-7s FAIL\n' "$name"
    tail -n 30 "$tmp/$name"
    status=1
  fi
done
[[ $status == 0 ]] && tail -n 1 "$tmp/tests"
exit $status
