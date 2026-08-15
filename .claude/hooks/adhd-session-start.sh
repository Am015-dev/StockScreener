#!/usr/bin/env bash
# SessionStart hook: makes the "I Have ADHD" skill (.claude/skills/i-have-adhd/)
# active from message one in every session on this repo, instead of relying on
# the reader to remember to type /i-have-adhd each time.
#
# Adapted from the skill's own upstream always-on hook
# (github.com/ayghri/i-have-adhd, hooks/always-on.sh), which gates on a flag
# file in the user's home directory (~/.claude/.i-have-adhd-always). That
# design fits a global, opt-in-per-user plugin install. This is a
# project-scoped skill committed straight into this repo, and Claude Code on
# the web runs each session in a fresh, ephemeral container — a home-directory
# flag would never survive to the next container, so it can't be what makes
# this "stick." Unconditional firing, keyed only off the skill file's own
# presence in the repo, is what actually persists here.
#
# Never blocks session start: any failure exits 0 rather than erroring.
set -euo pipefail

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &>/dev/null && pwd) || exit 0
skill_path="$script_dir/../skills/i-have-adhd/SKILL.md"
[ -f "$skill_path" ] || exit 0

# Strip the leading YAML frontmatter block (--- ... --- at the top of the
# file) — the ruleset body is what belongs in context, not the skill's own
# name/description/metadata fields.
body=$(awk '
  NR == 1 && /^---[[:space:]]*$/ { in_fm = 1; next }
  in_fm && /^---[[:space:]]*$/   { in_fm = 0; next }
  !in_fm { print }
' "$skill_path") || exit 0

printf 'ADHD MODE ACTIVE (always-on for this repo). The ruleset below applies to every response this session. Say "stop adhd mode" to turn it off for just this session — it reactivates next session, since the hook that loads it is committed to this repo, not this session.\n\n%s\n' \
  "$body"
