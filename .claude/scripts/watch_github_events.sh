#!/bin/bash
# Repo-wide GitHub event probe, shared by /builder and /reviewer.
#
# One /events call covers issues, PRs, issue comments, PR review comments and
# review submissions. Each new event is printed as one line, which the
# Monitor tool turns into a chat notification that re-invokes the loop.
#
# Usage: watch_github_events.sh <self-prefix> [state-dir]
#   self-prefix: "Builder" or "Task Reviewer" -- comments starting with this
#                (with or without ** markdown) are our own and skipped.
#   state-dir:   defaults to .claude/loop-state/<self-prefix, sanitized>
#
# Event ids are NOT monotonic across event types, so "max id seen" is unsafe;
# we keep an explicit seen-set on disk instead. State is gitignored -- it is
# machine-local loop state, not project history.

set -euo pipefail

SELF_PREFIX="${1:?usage: watch_github_events.sh <self-prefix> [state-dir]}"
REPO="$(gh repo view --json nameWithOwner -q .nameWithOwner)"

SLUG="$(echo "$SELF_PREFIX" | tr '[:upper:] ' '[:lower:]_')"
DIR="${2:-$(git rev-parse --show-toplevel)/.claude/loop-state/$SLUG}"
mkdir -p "$DIR"
STATE="$DIR/seen_events.txt"
RAW="$DIR/events.json"

INTERESTING='IssueCommentEvent|PullRequestReviewEvent|PullRequestReviewCommentEvent|IssuesEvent|PullRequestEvent'

# Seed with what already exists: the probe reports the future, not the archive.
# (Restart caveat: anything that happened during a restart blackout is seeded
# as "already seen" and will not be re-emitted.)
if [[ ! -s "$STATE" ]]; then
  gh api "repos/$REPO/events?per_page=100" --jq '.[].id' >"$STATE" 2>/dev/null || : >"$STATE"
fi

while true; do
  sleep 60
  gh api "repos/$REPO/events?per_page=30" >"$RAW" 2>/dev/null || continue

  # oldest-first so notifications read in chronological order
  jq -r '
    reverse | .[] | [
      .id,
      .type,
      (.payload.action // ""),
      (.payload.issue.number // .payload.pull_request.number // ""),
      ((.payload.comment.body // .payload.review.body // .payload.issue.title
        // .payload.pull_request.title // "") | gsub("[\r\n]+"; " ") | .[0:180])
    ] | @tsv' "$RAW" 2>/dev/null |
  while IFS=$'\t' read -r id type action num body; do
    grep -qxF "$id" "$STATE" && continue
    echo "$id" >>"$STATE"
    [[ "$type" =~ ^($INTERESTING)$ ]] || continue
    # asterisks are optional -- comments have been posted both ways
    [[ "$body" =~ ^(\*\*)?${SELF_PREFIX} ]] && continue
    echo "[$type/$action] #$num $body"
  done
done
