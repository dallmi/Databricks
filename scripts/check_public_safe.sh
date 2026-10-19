#!/usr/bin/env bash
# Blocks content that must not reach this public repository: the organisation's
# name (also as part of an identifier), real person identifiers, infrastructure
# identifiers and local paths.
#
# Values that belong to the real environment (workspace id, storage accounts,
# service principal ids, internal host names) cannot be listed here, because
# this file is public. Put them into the untracked file .local/denylist.txt,
# one fixed string per line; the check then blocks those as well.
#
# Usage:
#   scripts/check_public_safe.sh              tracked files in the working tree
#   scripts/check_public_safe.sh --staged     staged content (pre-commit hook)
#   scripts/check_public_safe.sh --message F  a commit message file (commit-msg hook)
#   scripts/check_public_safe.sh --history    every commit and commit message
#   scripts/check_public_safe.sh --files F..  the given files, tracked or not
#
# Activate the hooks once per clone:  git config core.hooksPath .githooks
set -u
cd "$(git rev-parse --show-toplevel)" || exit 2

PATTERNS=(
  '(^|[^A-Za-z])[Uu][Bb][Ss]'                              # organisation name at the start of a word or identifier
  '[a-z]U[Bb][Ss]([A-Z_]|$)'                               # ... or inside a camelCase identifier
  'adb-[0-9]{6,}'                                          # Databricks workspace id
  'abfss://[A-Za-z0-9-]+@[a-z0-9]'                         # storage path with a real account name
  '[a-z0-9]{3,24}\.(dfs|blob)\.core\.windows\.net'         # storage account host
  '(^|[^A-Za-z0-9])[tT][0-9]{6}([^0-9]|$)'                 # T-number
  '[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+\.(com|ch|net|org|de)'   # e-mail address
  '/Users/[A-Za-z]'                                        # local macOS path
  '[Cc]:\\Users\\'                                         # local Windows path
  '\.claude/projects/'                                     # local Claude memory path
)
# Synthetic examples and tool signatures that may appear.
ALLOW='t100200|T100200|noreply@anthropic\.com|@example\.(com|org)'
# Minified vendor code and embedded base64 bundles produce random matches.
EXCLUDE=(':!*.min.js' ':!scripts/check_public_safe.sh' ':!canvases-overview.json' ':!mockup/*.html')
DENYLIST=.local/denylist.txt

ARGS=()
for p in "${PATTERNS[@]}"; do ARGS+=(-e "$p"); done

hits=""
add() { if [ -n "$1" ]; then hits+="$1"$'\n'; fi; }
# Masks allowed tokens, then keeps only lines that still match a pattern,
# so an allowed example cannot hide a real value on the same line.
allowed_out() { sed -E "s/($ALLOW)/<ok>/g" | grep -E "${ARGS[@]}"; }
denylist_active() { [ -s "$DENYLIST" ]; }

mode=${1:---tree}
case "$mode" in
  --tree)
    add "$(git grep -nIE "${ARGS[@]}" -- . "${EXCLUDE[@]}" | allowed_out)"
    denylist_active && add "$(git grep -nIiF -f "$DENYLIST" -- . "${EXCLUDE[@]}")"
    ;;
  --staged)
    add "$(git grep --cached -nIE "${ARGS[@]}" -- . "${EXCLUDE[@]}" | allowed_out)"
    denylist_active && add "$(git grep --cached -nIiF -f "$DENYLIST" -- . "${EXCLUDE[@]}")"
    ;;
  --files)
    shift
    add "$(grep -nHE "${ARGS[@]}" "$@" | allowed_out)"
    denylist_active && add "$(grep -nHiF -f "$DENYLIST" "$@")"
    ;;
  --message)
    msg=$(grep -v '^#' "$2")
    add "$(printf '%s\n' "$msg" | grep -nE "${ARGS[@]}" | allowed_out)"
    denylist_active && add "$(printf '%s\n' "$msg" | grep -niF -f "$DENYLIST")"
    ;;
  --history)
    # Each hit is "<commit>:<path>"; it only counts if a matching line survives the allow-list.
    while IFS= read -r ref; do
      [ -n "$ref" ] || continue
      add "$(git grep -hIE "${ARGS[@]}" "${ref%%:*}" -- "${ref#*:}" | allowed_out | sed "s|^|$ref: |")"
    done < <(git grep -lIE "${ARGS[@]}" $(git rev-list --all) -- . "${EXCLUDE[@]}")
    add "$(git log --all --format='%h %B' | grep -nE "${ARGS[@]}" | allowed_out)"
    if denylist_active; then
      add "$(git grep -lIiF -f "$DENYLIST" $(git rev-list --all) -- . "${EXCLUDE[@]}")"
      add "$(git log --all --format='%h %B' | grep -niF -f "$DENYLIST")"
    fi
    ;;
  *)
    echo "unknown mode: $mode" >&2; exit 2 ;;
esac

if [ -n "$hits" ]; then
  echo "Not safe for the public repository:" >&2
  printf '%s' "$hits" | cut -c1-200 >&2
  echo >&2
  echo "Write the organisation name as __ORG__, use placeholders such as <workspace-url>" >&2
  echo "or <storage-account>, and synthetic person ids such as t100200." >&2
  exit 1
fi
denylist_active || echo "note: $DENYLIST not found, environment-specific values were not checked" >&2
exit 0
