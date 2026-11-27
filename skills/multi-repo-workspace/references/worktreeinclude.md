# `.worktreeinclude` contract

`.worktreeinclude` is a local workspace-hydration convention, not a Git feature. It declares selected ignored or untracked files that may be copied from a user-selected local checkout into its generated worktree.

## Source and trust boundary

- Read the manifest only from the `local_repo` supplied for that repository.
- Resolve every entry relative to the actual Git root of `local_repo`.
- Copy only into the corresponding generated worktree and preserve relative paths.
- A `.worktreeinclude` present only in the target branch is ordinary repository content and never controls access to local files.

## Syntax

The file is UTF-8, with one path pattern per line.

```text
# Local runtime configuration
.env.local

# Local tool context
.codex/
.agents/skills/example-*/
```

Supported entries:

- relative files;
- relative directories, copied recursively;
- glob patterns supported by Python's recursive `glob`, including `*`, `?`, character ranges, and `**`;
- blank lines and lines whose first non-space character is `#`.

Backslash escapes and negated patterns are not supported. Use forward slashes on every platform.

## Safety and conflicts

- Reject absolute paths, backslashes, `..`, and any path containing a `.git` segment.
- Reject a manifest whose match or resolved symlink escapes the local Git root.
- Do not traverse symlinked directories. A symlinked file is copied only when its resolved target remains inside the local Git root.
- Never overwrite a file already present in the generated worktree. This protects tracked branch content; report the path as skipped.
- A pattern with no matches is a warning and does not stop creation.
- Copy file metadata where the platform permits it.
- Reports contain path names and counts only, never file contents.

## Initialize a missing `.env.local`

After Git checkout and local file hydration, retain any existing `.env.local` path. If the path is absent, initialize it when all of these conditions hold.

- The target repository tracks a root `.worktreeinclude` containing a line with exactly `.env.local` after trimming whitespace.
- The target repository tracks a root `.env.example`. Both tracked files are ordinary files.
- Git ignores the target `.env.local` path.

Create the file exclusively and copy the template bytes as-is. This step applies to worktrees and clones. Projects that declare only `.env` obtain that file through local file hydration when available.

Preflight reports matches and missing patterns from the source checkout. The create report records the template initialization outcome in each repository's `env_local` field.

| Status | Outcome |
| --- | --- |
| `created` | Copied `.env.example` to `.env.local`. |
| `existing` | Kept the existing `.env.local` path. |
| `not_enabled` | The target lacks a tracked, ordinary manifest declaring `.env.local`. |
| `missing_template` | The target lacks a tracked, ordinary `.env.example`. |
| `not_ignored` | Git does not ignore the target `.env.local` path. |
