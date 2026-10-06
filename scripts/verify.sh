#!/usr/bin/env bash
# Forage local verification — single entrypoint for agents and hooks.
# No hosted CI. Do not add GitHub Actions workflows.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

fail() { echo "verify: FAIL: $*" >&2; exit 1; }
info() { echo "verify: $*"; }

# --- No GitHub Actions guard -------------------------------------------------
if [[ -d .github/workflows ]]; then
  mapfile -t _wf < <(find .github/workflows -type f \( -name '*.yml' -o -name '*.yaml' \) 2>/dev/null || true)
  if ((${#_wf[@]} > 0)); then
    fail "GitHub Actions workflows are not allowed (${#_wf[@]} under .github/workflows). Remove them; use ./scripts/verify.sh locally."
  fi
fi
info "no-GHA guard ok"

# --- Foundation files present ------------------------------------------------
[[ -f AGENTS.md ]] || fail "missing AGENTS.md"
[[ -f .cursor/rules/foundation.mdc ]] || fail "missing .cursor/rules/foundation.mdc"
[[ -f .cursor/skills/ship-change/SKILL.md ]] || fail "missing ship-change skill"
[[ -f docs/architecture.md ]] || fail "missing docs/architecture.md"
info "foundation files ok"

# Confirm rule frontmatter (Cursor requires .mdc + alwaysApply for always-on)
if ! awk 'BEGIN{fm=0;ok=0} /^---$/{fm++; next} fm==1 && /alwaysApply:[[:space:]]*true/{ok=1} END{exit ok?0:1}' \
  .cursor/rules/foundation.mdc; then
  fail "foundation.mdc must have YAML frontmatter with alwaysApply: true"
fi
info "foundation.mdc frontmatter ok"

# --- Python tooling (when present) -------------------------------------------
run_py() {
  if command -v uv >/dev/null 2>&1 && [[ -f pyproject.toml || -f uv.lock ]]; then
    uv run "$@"
  else
    "$@"
  fi
}

if [[ -f pyproject.toml ]] || [[ -f setup.cfg ]] || [[ -f setup.py ]]; then
  if command -v ruff >/dev/null 2>&1 || { command -v uv >/dev/null 2>&1 && uv run ruff --version >/dev/null 2>&1; }; then
    info "ruff check"
    run_py ruff check src tests
    info "ruff format --check"
    run_py ruff format --check src tests
  else
    info "ruff not configured; skip format/lint"
  fi

  if grep -qE '\[tool\.(mypy|pyright)\]' pyproject.toml 2>/dev/null; then
    if grep -q '\[tool\.mypy\]' pyproject.toml; then
      info "mypy"
      run_py mypy .
    fi
    if grep -q '\[tool\.pyright\]' pyproject.toml; then
      info "pyright"
      run_py pyright
    fi
  else
    info "no mypy/pyright config; skip typecheck"
  fi

  if [[ -d tests ]] || [[ -d test ]] || compgen -G "**/test_*.py" >/dev/null 2>&1; then
    info "pytest"
    run_py pytest -q
  else
    info "no tests yet; skip pytest"
  fi
else
  info "no Python project yet; skip Python checks"
fi

# --- Node tooling (when present) ---------------------------------------------
if [[ -f package.json ]]; then
  if [[ -f pnpm-lock.yaml ]] && command -v pnpm >/dev/null 2>&1; then
    PM=(pnpm)
  elif [[ -f yarn.lock ]] && command -v yarn >/dev/null 2>&1; then
    PM=(yarn)
  else
    PM=(npm)
  fi
  if grep -q '"lint"' package.json; then
    info "${PM[*]} run lint"
    "${PM[@]}" run lint
  fi
  if grep -q '"typecheck"' package.json; then
    info "${PM[*]} run typecheck"
    "${PM[@]}" run typecheck
  elif [[ -f tsconfig.json ]] && command -v npx >/dev/null 2>&1; then
    info "tsc --noEmit"
    npx tsc --noEmit
  fi
  if grep -qE '"test"[[:space:]]*:' package.json; then
    info "${PM[*]} test"
    "${PM[@]}" test
  fi
fi

info "OK"
