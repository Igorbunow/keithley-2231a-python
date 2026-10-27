#!/usr/bin/env bash
# Apply an incremental Git patch and stage exactly its declared file set.
set -euo pipefail

usage() {
    cat <<'USAGE'
Usage:
  Scripts/apply_incremental_patch.sh [--check] PATCH_FILE STAGE_PATHS_FILE

PATCH_FILE is a normal Git diff (use --binary for binary changes).
STAGE_PATHS_FILE lists the exact repository-relative file paths changed by the
patch, one per line; empty lines and lines starting with # are ignored.

--check validates the manifest and patch applicability without making changes.
Without --check, the patch is applied and only its declared paths are staged.
An already-staged frozen candidate is preserved. Tracked unstaged drift is
rejected; unrelated untracked files are not changed.

For file renames, generate the patch with renames disabled (git diff --no-renames).
USAGE
}
fail() {
    printf 'APPLY_INCREMENTAL_PATCH FAIL: %s\n' "$*" >&2
    exit 1
}

if [[ ${1:-} == --help || ${1:-} == -h ]]; then
    usage
    exit 0
fi
check_only=false
if [[ ${1:-} == --check ]]; then
    check_only=true
    shift
fi
[[ $# -eq 2 ]] || { usage >&2; exit 2; }

caller_pwd=$PWD
absolute_existing_file() {
    local input=$1 dir base
    if [[ $input == /* ]]; then
        dir=$(dirname -- "$input")
        base=$(basename -- "$input")
    else
        dir=$(dirname -- "$caller_pwd/$input")
        base=$(basename -- "$input")
    fi
    [[ -d $dir ]] || return 1
    dir=$(cd "$dir" && pwd -P)
    [[ -f $dir/$base ]] || return 1
    printf '%s/%s\n' "$dir" "$base"
}
patch=$(absolute_existing_file "$1") || fail "patch file not found: $1"
paths=$(absolute_existing_file "$2") || fail "stage-paths file not found: $2"

root=$(git rev-parse --show-toplevel 2>/dev/null) || fail 'not inside a Git worktree'
cd "$root"
git diff --check
git diff --cached --check
if ! git diff --quiet --ignore-submodules --; then
    fail 'tracked unstaged changes exist before patch application'
fi

before_sha=$(git diff --cached --binary | sha256sum | awk '{print $1}')
patch_sha=$(sha256sum "$patch" | awk '{print $1}')
tmp=$(mktemp -d "${TMPDIR:-/tmp}/apply-incremental-patch.XXXXXX")
trap 'rm -rf "$tmp"' EXIT

manifest_raw=$tmp/manifest.raw
patch_raw=$tmp/patch.raw
: > "$manifest_raw"
: > "$patch_raw"

while IFS= read -r path || [[ -n $path ]]; do
    [[ -n $path && $path != \#* ]] || continue
    case $path in
        /*|./*|../*|*/../*|*/..|.|..|.git|.git/*)
            fail "unsafe path in stage manifest: $path" ;;
    esac
    [[ $path != *$'\r'* && $path != *$'\t'* && $path != *$'\n'* ]] || \
        fail "unsupported control character in stage path: $path"
    printf '%s\n' "$path" >> "$manifest_raw"
done < "$paths"
[[ -s $manifest_raw ]] || fail 'stage-paths manifest is empty'
if [[ -n $(LC_ALL=C sort "$manifest_raw" | uniq -d) ]]; then
    fail 'stage-paths manifest contains duplicate paths'
fi
LC_ALL=C sort "$manifest_raw" > "$tmp/manifest.sorted"

# A failure to parse the diff must never silently produce an empty path set.
git apply --numstat -z -- "$patch" > "$tmp/numstat" || fail 'cannot parse Git patch'
# With -z, ordinary changes are added<TAB>deleted<TAB>path<NUL>.
# Rename/copy changes instead have an empty path plus further NUL records.
while IFS= read -r -d '' record; do
    [[ $record == *$'\t'*$'\t'* ]] || fail 'invalid numstat record'
    path=${record#*$'\t'}
    path=${path#*$'\t'}
    [[ -n $path ]] || fail 'rename/copy patches are unsupported; use git diff --no-renames'
    [[ $path != *$'\r'* && $path != *$'\t'* && $path != *$'\n'* ]] || \
        fail 'unsupported control character in patch path'
    printf '%s\n' "$path" >> "$patch_raw"
done < "$tmp/numstat"
[[ -s $patch_raw ]] || fail 'patch contains no file changes'
if [[ -n $(LC_ALL=C sort "$patch_raw" | uniq -d) ]]; then
    fail 'patch reports duplicate paths'
fi
LC_ALL=C sort "$patch_raw" > "$tmp/patch.sorted"
if ! cmp -s "$tmp/manifest.sorted" "$tmp/patch.sorted"; then
    printf '%s\n' '--- declared stage paths ---' >&2
    cat "$tmp/manifest.sorted" >&2
    printf '%s\n' '--- patch paths ---' >&2
    cat "$tmp/patch.sorted" >&2
    fail 'patch path set does not exactly match stage-paths manifest'
fi

git apply --check -- "$patch"
if [[ $check_only == true ]]; then
    printf 'APPLY_INCREMENTAL_PATCH patch_sha=%s paths=%s status=CHECK_PASS (no changes)\n' \
        "$patch_sha" "$(wc -l < "$manifest_raw" | tr -d ' ')"
    exit 0
fi

git apply -- "$patch"
while IFS= read -r path; do
    if [[ -e $path || -L $path ]]; then
        git add -A -- "$path"
    else
        git add -u -- "$path"
    fi
done < "$manifest_raw"

if ! git diff --quiet --ignore-submodules --; then
    git status --short >&2
    fail 'tracked unstaged changes remain after staging declared paths'
fi
git diff --check
git diff --cached --check
after_sha=$(git diff --cached --binary | sha256sum | awk '{print $1}')
printf 'APPLY_INCREMENTAL_PATCH before_staged_sha=%s patch_sha=%s after_staged_sha=%s paths=%s status=PASS\n' \
    "$before_sha" "$patch_sha" "$after_sha" "$(wc -l < "$manifest_raw" | tr -d ' ')"
