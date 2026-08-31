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
