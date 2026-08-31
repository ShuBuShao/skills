#!/usr/bin/env python3
"""Safely create a multi-repository workspace from a JSON specification."""

from __future__ import annotations

import argparse
import glob
import json
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Optional, Sequence
from urllib.parse import urlsplit, urlunsplit


class WorkspaceError(Exception):
    """An expected validation or execution error."""


@dataclass(frozen=True)
class Repository:
    name: str
    url: str
    directory: str
    branch: str
    base_branch: Optional[str]
    local_repo: Optional[Path]


@dataclass(frozen=True)
class WorkspaceSpec:
    workspace: Path
    name: str
    branch: str
    primary: str
    repositories: tuple[Repository, ...]


@dataclass(frozen=True)
class HydrationPlan:
    manifest: Optional[Path]
    patterns: tuple[str, ...]
    files: tuple[Path, ...]
    missing: tuple[str, ...]


@dataclass(frozen=True)
class CheckoutPlan:
    mode: str
    start_point: str
    fetch_branch: Optional[str]


@dataclass(frozen=True)
class RepositoryPlan:
    checkout: CheckoutPlan
    hydration: HydrationPlan


def git(
    args: list[str], cwd: Optional[Path] = None, timeout: Optional[int] = None
) -> str:
    env = os.environ.copy()
    env.setdefault("GIT_TERMINAL_PROMPT", "0")
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=cwd,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as error:
        raise WorkspaceError("git is required but was not found") from error
    except subprocess.TimeoutExpired as error:
        raise WorkspaceError("git command timed out") from error

    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or "unknown git error"
        raise WorkspaceError(detail)
    return completed.stdout.strip()


def git_succeeds(args: list[str], cwd: Optional[Path] = None) -> bool:
    env = os.environ.copy()
    env.setdefault("GIT_TERMINAL_PROMPT", "0")
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=cwd,
            env=env,
            text=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            timeout=15,
            check=False,
        )
    except FileNotFoundError as error:
        raise WorkspaceError("git is required but was not found") from error
    except subprocess.TimeoutExpired as error:
        raise WorkspaceError("git command timed out") from error
    if completed.returncode not in (0, 1):
        raise WorkspaceError(completed.stderr.strip() or "git reference check failed")
    return completed.returncode == 0


def require_string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise WorkspaceError(f"{field} must be a non-empty string")
    return value.strip()


def safe_relative_path(value: Any, field: str) -> str:
    relative = require_string(value, field)
    if "\\" in relative:
        raise WorkspaceError(f"{field} must use forward slashes")
    path = PurePosixPath(relative)
    windows = PureWindowsPath(relative)
    if (
        path.is_absolute()
        or windows.is_absolute()
        or bool(windows.drive)
        or relative in {".", ".."}
        or ".." in path.parts
    ):
        raise WorkspaceError(f"{field} must be a safe relative path")
    if not path.parts:
        raise WorkspaceError(f"{field} must not be empty")
    return path.as_posix().rstrip("/")


def validate_branch(value: str, field: str) -> str:
    branch = require_string(value, field)
    if branch.startswith("-"):
        raise WorkspaceError(f"{field} must not start with '-'")
    git(["check-ref-format", "--branch", branch], timeout=15)
    return branch


def redact_url(url: str) -> str:
    if "://" not in url:
        return url
    parsed = urlsplit(url)
    if parsed.username is None and parsed.password is None:
        return url
    host = parsed.hostname or ""
    if parsed.port:
        host = f"{host}:{parsed.port}"
    return urlunsplit((parsed.scheme, host, parsed.path, parsed.query, parsed.fragment))


def validate_url(url: str, field: str) -> str:
    remote = require_string(url, field)
    if remote.startswith("-"):
        raise WorkspaceError(f"{field} must not start with '-'")
    if "://" in remote:
        parsed = urlsplit(remote)
        if parsed.username is not None or parsed.password is not None:
            raise WorkspaceError(f"{field} must not contain embedded credentials")
    return remote


def git_root(path: Path, field: str) -> Path:
    if not path.exists() or not path.is_dir():
        raise WorkspaceError(f"{field} is not an existing directory: {path}")
    return Path(git(["rev-parse", "--show-toplevel"], cwd=path, timeout=15)).resolve()


def load_spec(path: Path) -> WorkspaceSpec:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise WorkspaceError(f"spec file does not exist: {path}") from error
    except (OSError, json.JSONDecodeError) as error:
        raise WorkspaceError(f"cannot read spec file: {error}") from error

    if not isinstance(data, dict):
        raise WorkspaceError("spec root must be a JSON object")

    workspace_value = Path(require_string(data.get("workspace"), "workspace")).expanduser()
    if not workspace_value.is_absolute():
        raise WorkspaceError("workspace must be an absolute path")
    workspace = workspace_value.resolve()
    name = require_string(data.get("name"), "name")
    if name in {".", ".."} or "/" in name or "\\" in name:
        raise WorkspaceError("name must be a file-name-safe workspace name")
    branch = validate_branch(data.get("branch"), "branch")
    primary = require_string(data.get("primary"), "primary")
    raw_repositories = data.get("repositories")
    if not isinstance(raw_repositories, list) or not raw_repositories:
        raise WorkspaceError("repositories must be a non-empty array")

    repositories: list[Repository] = []
    names: set[str] = set()
    directories: set[str] = set()
    for index, raw in enumerate(raw_repositories):
        if not isinstance(raw, dict):
            raise WorkspaceError(f"repositories[{index}] must be an object")
        prefix = f"repositories[{index}]"
        repo_name = require_string(raw.get("name"), f"{prefix}.name")
        if repo_name in names:
            raise WorkspaceError(f"duplicate repository name: {repo_name}")
        names.add(repo_name)

        directory = safe_relative_path(raw.get("directory", repo_name), f"{prefix}.directory")
        if directory in directories:
            raise WorkspaceError(f"duplicate repository directory: {directory}")
        directories.add(directory)

        repo_branch = validate_branch(raw.get("branch", branch), f"{prefix}.branch")
        base_value = raw.get("base_branch")
        base_branch = (
            validate_branch(base_value, f"{prefix}.base_branch") if base_value is not None else None
        )

        local_value = raw.get("local_repo")
        local_repo = None
        if local_value is not None:
            local_path = Path(require_string(local_value, f"{prefix}.local_repo")).expanduser()
            local_repo = git_root(local_path, f"{prefix}.local_repo")

        url_value = raw.get("url")
        if local_repo is not None:
            try:
                url_value = git(["remote", "get-url", "origin"], cwd=local_repo, timeout=15)
            except WorkspaceError as error:
                raise WorkspaceError(
                    f"{prefix}.local_repo requires an origin remote"
                ) from error
        elif url_value is None:
            raise WorkspaceError(f"{prefix} requires url or local_repo")
        url = validate_url(url_value, f"{prefix}.url")

        repositories.append(
            Repository(
                name=repo_name,
                url=url,
                directory=directory,
                branch=repo_branch,
                base_branch=base_branch,
                local_repo=local_repo,
            )
        )

    if primary not in names:
        raise WorkspaceError("primary must match one repository name")
    directory_paths = [PurePosixPath(directory) for directory in directories]
    for index, left in enumerate(directory_paths):
        for right in directory_paths[index + 1 :]:
            if left in right.parents or right in left.parents:
                raise WorkspaceError(
                    f"repository directories must not overlap: {left.as_posix()} and {right.as_posix()}"
                )
    if f"{name}.code-workspace" in directories:
        raise WorkspaceError("workspace file conflicts with a repository directory")

    return WorkspaceSpec(
        workspace=workspace,
        name=name,
        branch=branch,
        primary=primary,
        repositories=tuple(repositories),
    )


def is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def validate_pattern(pattern: str, line_number: int) -> str:
    if "\\" in pattern:
        raise WorkspaceError(f".worktreeinclude line {line_number}: backslashes are not allowed")
    posix = PurePosixPath(pattern)
    windows = PureWindowsPath(pattern)
    if pattern.startswith("!"):
        raise WorkspaceError(
            f".worktreeinclude line {line_number}: negated patterns are not supported"
        )
    if (
        posix.is_absolute()
        or windows.is_absolute()
        or bool(windows.drive)
        or ".." in posix.parts
        or pattern.rstrip("/") == "."
    ):
        raise WorkspaceError(f".worktreeinclude line {line_number}: unsafe path {pattern!r}")
    if ".git" in posix.parts:
        raise WorkspaceError(f".worktreeinclude line {line_number}: .git cannot be copied")
    return pattern


def directory_files(directory: Path, root: Path) -> list[Path]:
    files: list[Path] = []
    for current, dir_names, file_names in os.walk(directory, followlinks=False):
        current_path = Path(current)
        dir_names[:] = [
            name
            for name in dir_names
            if name != ".git" and not (current_path / name).is_symlink()
        ]
        for name in file_names:
            candidate = current_path / name
            if ".git" in candidate.relative_to(root).parts:
                continue
            if candidate.is_symlink() and not is_within(candidate, root):
                raise WorkspaceError(f"symlink escapes local repository: {candidate.relative_to(root)}")
            files.append(candidate)
    return files


def hydration_plan(repository: Repository) -> HydrationPlan:
    if repository.local_repo is None:
        return HydrationPlan(None, (), (), ())
    root = repository.local_repo
    manifest = root / ".worktreeinclude"
    if not manifest.is_file():
        return HydrationPlan(None, (), (), ())

    try:
        lines = manifest.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as error:
        raise WorkspaceError(f"cannot read {manifest}: {error}") from error

    patterns: list[str] = []
    missing: list[str] = []
    files: dict[str, Path] = {}
    for line_number, raw_line in enumerate(lines, start=1):
        pattern = raw_line.strip()
        if not pattern or pattern.startswith("#"):
            continue
        pattern = validate_pattern(pattern, line_number)
        patterns.append(pattern)
        matches = [Path(match) for match in glob.glob(str(root / pattern), recursive=True)]
        if not matches:
            missing.append(pattern)
            continue
        for match in matches:
            if match.resolve() == root.resolve():
                raise WorkspaceError(
                    f".worktreeinclude line {line_number}: pattern selects repository root"
                )
            if not is_within(match, root):
                raise WorkspaceError(f"manifest match escapes local repository: {pattern!r}")
            candidates = directory_files(match, root) if match.is_dir() and not match.is_symlink() else [match]
            for candidate in candidates:
                if candidate.is_dir():
                    continue
                if candidate.is_symlink() and not is_within(candidate, root):
                    raise WorkspaceError(
                        f"symlink escapes local repository: {candidate.relative_to(root)}"
                    )
                relative = candidate.relative_to(root).as_posix()
                if ".git" in PurePosixPath(relative).parts:
                    raise WorkspaceError(f"manifest resolved inside .git: {relative}")
                files[relative] = candidate

    ordered = tuple(files[key] for key in sorted(files))
    return HydrationPlan(manifest, tuple(patterns), ordered, tuple(missing))


def remote_branch_exists(url: str, branch: str) -> bool:
    output = git(["ls-remote", "--heads", url, f"refs/heads/{branch}"], timeout=60)
    return bool(output)


def local_branch_exists(repository: Repository, branch: str) -> bool:
    if repository.local_repo is None:
        return False
    return git_succeeds(
        ["show-ref", "--verify", "--quiet", f"refs/heads/{branch}"],
        cwd=repository.local_repo,
    )


def branch_worktree(repository: Repository, branch: str) -> Optional[Path]:
    if repository.local_repo is None:
        return None
    output = git(["worktree", "list", "--porcelain"], cwd=repository.local_repo, timeout=15)
    current_path: Optional[Path] = None
    expected_ref = f"refs/heads/{branch}"
    for line in [*output.splitlines(), ""]:
        if line.startswith("worktree "):
            current_path = Path(line.removeprefix("worktree "))
        elif line == f"branch {expected_ref}":
            return current_path
        elif not line:
            current_path = None
    return None


def checkout_plan(repository: Repository) -> CheckoutPlan:
    if repository.local_repo is None:
        if remote_branch_exists(repository.url, repository.branch):
            return CheckoutPlan("clone-branch", repository.branch, None)
        if repository.base_branch is None:
            raise WorkspaceError(
                f"remote branch {repository.branch!r} does not exist and base_branch is not set"
            )
        if not remote_branch_exists(repository.url, repository.base_branch):
            raise WorkspaceError(
                f"remote branch {repository.branch!r} and base branch "
                f"{repository.base_branch!r} do not exist"
            )
        return CheckoutPlan(
            "clone-base-create-local-branch", repository.base_branch, None
        )

    occupied_path = branch_worktree(repository, repository.branch)
    if local_branch_exists(repository, repository.branch):
        if occupied_path is not None:
            raise WorkspaceError(
                f"branch {repository.branch!r} is already checked out at {occupied_path}"
            )
        return CheckoutPlan(
            "worktree-existing-local-branch", repository.branch, None
        )

    if remote_branch_exists(repository.url, repository.branch):
        return CheckoutPlan(
            "worktree-track-remote-branch",
            f"origin/{repository.branch}",
            repository.branch,
        )

    if repository.base_branch is None:
        raise WorkspaceError(
            f"branch {repository.branch!r} is unavailable locally and remotely, "
            "and base_branch is not set"
        )
    if local_branch_exists(repository, repository.base_branch):
        return CheckoutPlan(
            "worktree-create-local-branch-from-local-base",
            repository.base_branch,
            None,
        )
    if remote_branch_exists(repository.url, repository.base_branch):
        return CheckoutPlan(
            "worktree-create-local-branch-from-remote-base",
            f"origin/{repository.base_branch}",
            repository.base_branch,
        )
    raise WorkspaceError(
        f"branch {repository.branch!r} and base branch {repository.base_branch!r} "
        "are unavailable locally and remotely"
    )


def summarized_paths(paths: Sequence[str], limit: int = 200) -> dict[str, Any]:
    values = list(paths)
    return {"count": len(values), "paths": values[:limit], "truncated": len(values) > limit}


def inspect_spec(spec: WorkspaceSpec) -> tuple[dict[str, Any], dict[str, RepositoryPlan]]:
    errors: list[str] = []
    warnings: list[str] = []
    plans: dict[str, RepositoryPlan] = {}

    if spec.workspace.exists():
        errors.append(f"workspace destination already exists: {spec.workspace}")

    repositories: list[dict[str, Any]] = []
    for repository in spec.repositories:
        try:
            if repository.local_repo is not None and (
                is_within(spec.workspace, repository.local_repo)
                or is_within(repository.local_repo, spec.workspace)
            ):
                raise WorkspaceError(
                    "workspace destination and local repository must not overlap"
                )
            checkout = checkout_plan(repository)
            hydration = hydration_plan(repository)
            plans[repository.name] = RepositoryPlan(checkout, hydration)
            for pattern in hydration.missing:
                warnings.append(f"{repository.name}: no local match for {pattern!r}")
            relative_files = (
                [path.relative_to(repository.local_repo).as_posix() for path in hydration.files]
                if repository.local_repo
                else []
            )
            repositories.append(
                {
                    "name": repository.name,
                    "url": redact_url(repository.url),
                    "directory": repository.directory,
                    "branch": repository.branch,
                    "base_branch": repository.base_branch,
                    "mode": checkout.mode,
                    "local_repo": str(repository.local_repo) if repository.local_repo else None,
                    "hydration": {
                        "manifest": str(hydration.manifest) if hydration.manifest else None,
                        "patterns": list(hydration.patterns),
                        "files": summarized_paths(relative_files),
                        "missing": list(hydration.missing),
                    },
                }
            )
        except WorkspaceError as error:
            errors.append(f"{repository.name}: {error}")

    return (
        {
            "status": "error" if errors else "ok",
            "workspace": str(spec.workspace),
            "workspace_file": str(spec.workspace / f"{spec.name}.code-workspace"),
            "primary": spec.primary,
            "repositories": repositories,
            "warnings": warnings,
            "errors": errors,
        },
        plans,
    )


def copy_hydration(repository: Repository, plan: HydrationPlan, target: Path) -> dict[str, Any]:
    copied: list[str] = []
    skipped: list[str] = []
    if repository.local_repo is None:
        return {
            "copied": summarized_paths(copied),
            "skipped": summarized_paths(skipped),
            "missing": list(plan.missing),
        }

    source_root = repository.local_repo
    for source in plan.files:
        relative = source.relative_to(source_root)
        if not source.exists() or not source.is_file():
            raise WorkspaceError(f"local hydration source changed or disappeared: {relative.as_posix()}")
        if not is_within(source, source_root):
            raise WorkspaceError(f"local hydration source escapes repository: {relative.as_posix()}")
        destination = target / relative
        if not is_within(destination.parent, target):
            raise WorkspaceError(f"copy destination escapes clone: {relative.as_posix()}")
        if destination.exists() or destination.is_symlink():
            skipped.append(relative.as_posix())
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination, follow_symlinks=True)
        copied.append(relative.as_posix())

    return {
        "copied": summarized_paths(copied),
        "skipped": summarized_paths(skipped),
        "missing": list(plan.missing),
    }


def fetch_remote_branch(repository: Repository, branch: str) -> None:
    if repository.local_repo is None:
        raise WorkspaceError(f"{repository.name}: local repository is required for fetch")
    git(
        [
            "fetch",
            "--no-tags",
            "origin",
            f"+refs/heads/{branch}:refs/remotes/origin/{branch}",
        ],
        cwd=repository.local_repo,
    )


def create_checkout(repository: Repository, plan: CheckoutPlan, target: Path) -> str:
    if repository.local_repo is None:
        if plan.mode == "clone-branch":
            git(
                [
                    "clone",
                    "--branch",
                    repository.branch,
                    "--single-branch",
                    "--",
                    repository.url,
                    str(target),
                ]
            )
        else:
            git(
                [
                    "clone",
                    "--branch",
                    plan.start_point,
                    "--single-branch",
                    "--",
                    repository.url,
                    str(target),
                ]
            )
            git(["switch", "-c", repository.branch], cwd=target)
        return plan.mode

    if plan.fetch_branch is not None:
        fetch_remote_branch(repository, plan.fetch_branch)
    if plan.mode == "worktree-existing-local-branch":
        git(
            ["worktree", "add", "--", str(target), repository.branch],
            cwd=repository.local_repo,
        )
    elif plan.mode == "worktree-track-remote-branch":
        git(
            [
                "worktree",
                "add",
                "--track",
                "-b",
                repository.branch,
                "--",
                str(target),
                plan.start_point,
            ],
            cwd=repository.local_repo,
        )
    else:
        git(
            [
                "worktree",
                "add",
                "-b",
                repository.branch,
                "--",
                str(target),
                plan.start_point,
            ],
            cwd=repository.local_repo,
        )
    return plan.mode


def create_workspace(spec: WorkspaceSpec, plans: dict[str, RepositoryPlan]) -> dict[str, Any]:
    parent = spec.workspace.parent
    parent.mkdir(parents=True, exist_ok=True)
    if spec.workspace.exists():
        raise WorkspaceError(f"workspace destination already exists: {spec.workspace}")
    staging = Path(tempfile.mkdtemp(prefix=f".{spec.workspace.name}.creating-", dir=parent))
    results: list[dict[str, Any]] = []
    linked_worktrees: list[dict[str, str]] = []
    workspace_moved = False

    try:
        for repository in spec.repositories:
            target = staging / repository.directory
            target.parent.mkdir(parents=True, exist_ok=True)
            repository_plan = plans[repository.name]
            mode = create_checkout(repository, repository_plan.checkout, target)
            if repository.local_repo is not None:
                linked_worktrees.append(
                    {
                        "name": repository.name,
                        "backing_repo": str(repository.local_repo),
                        "path": str(target),
                    }
                )

            current_branch = git(["branch", "--show-current"], cwd=target, timeout=15)
            if current_branch != repository.branch:
                raise WorkspaceError(
                    f"{repository.name}: expected branch {repository.branch!r}, got {current_branch!r}"
                )
            hydration = copy_hydration(repository, repository_plan.hydration, target)
            results.append(
                {
                    "name": repository.name,
                    "directory": repository.directory,
                    "branch": current_branch,
                    "mode": mode,
                    "backing_repo": (
                        str(repository.local_repo) if repository.local_repo else None
                    ),
                    "hydration": hydration,
                }
            )

        positions = {repository.name: index for index, repository in enumerate(spec.repositories)}
        ordered = sorted(
            spec.repositories,
            key=lambda repository: (repository.name != spec.primary, positions[repository.name]),
        )
        workspace_data = {
            "folders": [
                {"name": repository.name, "path": repository.directory} for repository in ordered
            ]
        }
        staging_workspace = staging / f"{spec.name}.code-workspace"
        staging_workspace.write_text(
            json.dumps(workspace_data, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        if spec.workspace.exists():
            raise WorkspaceError(f"workspace destination appeared during creation: {spec.workspace}")
        staging.rename(spec.workspace)
        workspace_moved = True
        for linked_worktree in linked_worktrees:
            linked_worktree["path"] = str(
                spec.workspace
                / next(
                    repository.directory
                    for repository in spec.repositories
                    if repository.name == linked_worktree["name"]
                )
            )
        for repository in spec.repositories:
            if repository.local_repo is None:
                continue
            final_target = spec.workspace / repository.directory
            git(
                ["worktree", "repair", str(final_target)],
                cwd=repository.local_repo,
                timeout=30,
            )
            actual_root = git(["rev-parse", "--show-toplevel"], cwd=final_target, timeout=15)
            if Path(actual_root).resolve() != final_target.resolve():
                raise WorkspaceError(
                    f"{repository.name}: repaired worktree resolves to {actual_root}"
                )
        return {
            "status": "ok",
            "workspace": str(spec.workspace),
            "workspace_file": str(spec.workspace / staging_workspace.name),
            "primary": spec.primary,
            "repositories": results,
            "linked_worktrees": linked_worktrees,
            "warnings": [],
            "errors": [],
        }
    except Exception as error:
        message = str(error) if isinstance(error, WorkspaceError) else f"unexpected error: {error}"
        return {
            "status": "error",
            "workspace": str(spec.workspace),
            "staging": None if workspace_moved else str(staging),
            "partial_workspace": str(spec.workspace) if workspace_moved else None,
            "repositories": results,
            "linked_worktrees": linked_worktrees,
            "warnings": [],
            "errors": [message],
        }


def emit(report: dict[str, Any]) -> None:
    print(json.dumps(report, indent=2, ensure_ascii=False))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create a worktree-first multi-repository workspace"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("preflight", "create"):
        child = subparsers.add_parser(command)
        child.add_argument("--spec", required=True, type=Path, help="Path to the JSON spec")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        spec = load_spec(args.spec)
        report, plans = inspect_spec(spec)
        if args.command == "preflight" or report["status"] != "ok":
            emit(report)
            return 0 if report["status"] == "ok" else 2
        result = create_workspace(spec, plans)
        emit(result)
        return 0 if result["status"] == "ok" else 1
    except WorkspaceError as error:
        emit({"status": "error", "warnings": [], "errors": [str(error)]})
        return 2
    except Exception as error:
        emit(
            {
                "status": "error",
                "warnings": [],
                "errors": [f"unexpected error: {error}"],
            }
        )
        return 1


if __name__ == "__main__":
    sys.exit(main())
