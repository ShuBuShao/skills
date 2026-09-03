---
name: multi-repo-workspace
description: Create a coordinated VS Code and Claude Code multi-repository workspace by using Git worktrees from user-selected local repositories first, cloning repositories without a local checkout, optionally copying local files declared by .worktreeinclude, and generating Chinese workspace-level Claude instructions. Use when the user asks to create, rebuild, or automate a multi-repository development workspace. Do not use for an ordinary single-repository clone or standalone worktree maintenance.
---

# Multi-Repo Workspace

Create repository checkouts inside one wrapper directory, then generate a `.code-workspace` file and a Chinese workspace-level `CLAUDE.md`. Use a linked worktree whenever the user supplies a local repository; clone only repositories without `local_repo`. Treat one repository as the primary Codex project; do not merge repository instructions, skills, or Codex configuration.

The wrapper directory is the Claude Code workspace root. Its `CLAUDE.md` treats the folders in the generated `.code-workspace` file as the current project inventory and the first folder as the default primary project. Keep repository-specific Claude instructions in each repository and let Claude discover them when working there; do not copy or merge them into the wrapper.

Use the bundled `scripts/create_workspace.py` for preflight and creation. Do not reimplement its clone or copy logic with ad hoc shell commands.

The script requires Git and Python 3.9 or newer. It uses only the Python standard library.

## Required input

Collect these values without asking the user to repeat anything already supplied:

- workspace name and destination path;
- shared branch name;
- primary repository name;
- repositories, each with a name and either a local Git checkout when one is available or a remote URL otherwise;
- optional per-repository directory, branch override, and base branch.

`local_repo` selects worktree mode and its `origin` remote is authoritative. Its Git object database and local branches back the new checkout. A repository without `local_repo` requires `url` and uses clone mode.

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

If the requested branch already exists locally, worktree mode uses it without contacting the remote. If it exists only on `origin`, the script fetches that branch and creates a tracking branch. `base_branch` is required when the requested branch exists neither locally nor remotely; the script creates the requested branch from the local base when available, otherwise from the fetched remote base. Clone mode follows the equivalent remote-branch and confirmed-base behavior. The script never pushes or creates a remote branch.

## Workflow

1. Run `python3 scripts/create_workspace.py preflight --spec <temporary-spec>` from this Skill directory.
2. Read the JSON report. Resolve every error before continuing. Missing include entries are warnings, not errors.
3. Show one concise confirmation containing the destination, primary repository, per-repository worktree-or-clone mode, backing local repository or remote URL, branch behavior, and hydration patterns/counts. Never display copied file contents.
4. After confirmation, run `python3 scripts/create_workspace.py create --spec <temporary-spec>`.
5. Verify the final JSON report says `ok`, every repository reports the requested branch, and both the `.code-workspace` file and the Chinese wrapper-level `CLAUDE.md` exist.
6. Report copied, skipped, and missing paths by name only. Offer to open the workspace only when the user asks.
7. Tell the user they can enter the wrapper directory and run `claude`; do not add `--add-dir` arguments for repositories inside that wrapper.

If the destination already exists, a requested branch is already checked out in another worktree, the requested and confirmed base branches are unavailable, or a manifest contains an unsafe path, stop without creating the final workspace. Never bypass branch occupancy with `--force`.

Worktrees depend on the supplied local repository's common Git directory. Do not move or delete that backing repository while the generated workspace is in use. The script repairs Git's recorded worktree paths after moving the completed wrapper out of staging.

## Local file hydration

Hydration runs only when `local_repo` is provided and that selected checkout contains `.worktreeinclude`. It applies to the generated worktree after checkout. Read [references/worktreeinclude.md](references/worktreeinclude.md) whenever hydration is active.

The manifest in the local checkout is authoritative. Never use a remote branch's manifest to decide which local files to copy. Do not copy all ignored or untracked files as a fallback.

## Boundaries

- Do not initialize Git in the wrapper directory.
- Do not create a persistent workspace manifest such as `workspace.yaml` or generate a Claude launcher script.
- Do not modify global or project Codex configuration.
- Do not merge `AGENTS.md`, skills, or configuration across repositories.
- Do not install dependencies, start services, open an IDE, commit, or push unless separately requested.
- Do not automatically remove a failed staging directory or linked worktree. Report its path and backing repository so the user can inspect it and use `git worktree remove` deliberately.
