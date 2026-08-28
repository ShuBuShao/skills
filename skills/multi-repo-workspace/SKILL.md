---
name: multi-repo-workspace
description: Create a coordinated VS Code multi-root workspace by cloning one branch across multiple Git repositories and optionally copying ignored local files declared by .worktreeinclude from user-selected local checkouts. Use when the user asks to create, rebuild, or automate a multi-repository development workspace. Do not use for an ordinary single-repository clone or for Git worktree management.
---

# Multi-Repo Workspace

Create independent Git clones inside one wrapper directory, then generate a `.code-workspace` file. Treat one repository as the primary Codex project; do not merge repository instructions, skills, or Codex configuration.

Use the bundled `scripts/create_workspace.py` for preflight and creation. Do not reimplement its clone or copy logic with ad hoc shell commands.

The script requires Git and Python 3.9 or newer. It uses only the Python standard library.

## Required input

Collect these values without asking the user to repeat anything already supplied:

- workspace name and destination path;
- shared branch name;
- primary repository name;
- repositories, each with a name and either a remote URL or a local Git checkout;
- optional per-repository directory, branch override, base branch, and local checkout used for hydration.

When a URL is omitted, the script derives it from the local checkout's `remote.origin.url`. The local checkout is otherwise only a source for `.worktreeinclude` files; Git history is cloned from the remote URL.

Do not put credentials in the spec. Rely on the user's existing Git credential helper, SSH agent, or authenticated remote configuration.

## Spec file

Create a temporary JSON file outside repositories and the requested workspace. Use this shape:

```json
{
  "workspace": "/absolute/path/to/workspace",
  "name": "feature-suite",
  "branch": "feature/shared-name",
  "primary": "frontend",
  "repositories": [
    {
      "name": "frontend",
      "url": "git@github.com:example/frontend.git",
      "directory": "frontend",
      "local_repo": "/absolute/path/to/existing/frontend"
    },
    {
      "name": "backend",
      "url": "git@github.com:example/backend.git",
      "base_branch": "main"
    }
  ]
}
```

`base_branch` is used only when the requested branch does not exist remotely. In that case the script clones the confirmed base branch and creates the requested branch locally; it never pushes or creates a remote branch.

## Workflow

1. Run `python3 scripts/create_workspace.py preflight --spec <temporary-spec>` from this Skill directory.
2. Read the JSON report. Resolve every error before continuing. Missing include entries are warnings, not errors.
3. Show one concise confirmation containing the destination, primary repository, repository URL/branch mapping, missing-branch behavior, and hydration patterns/counts. Never display copied file contents.
4. After confirmation, run `python3 scripts/create_workspace.py create --spec <temporary-spec>`.
5. Verify the final JSON report says `ok`, every repository reports the requested branch, and the `.code-workspace` file exists.
6. Report copied, skipped, and missing paths by name only. Offer to open the workspace only when the user asks.

If the destination already exists, a remote branch and confirmed base branch are both unavailable, or a manifest contains an unsafe path, stop without creating the final workspace.

## Local file hydration

Hydration runs only when `local_repo` is provided and that checkout contains `.worktreeinclude`. Read [references/worktreeinclude.md](references/worktreeinclude.md) whenever hydration is active.

The manifest in the local checkout is authoritative. Never use a remote branch's manifest to decide which local files to copy. Do not copy all ignored or untracked files as a fallback.

## Boundaries

- Do not use `git worktree`.
- Do not initialize Git in the wrapper directory.
- Do not modify global or project Codex configuration.
- Do not merge `AGENTS.md`, skills, or configuration across repositories.
- Do not install dependencies, start services, open an IDE, commit, or push unless separately requested.
- Do not delete a failed staging directory automatically; report its path so the user can inspect or remove it explicitly.
