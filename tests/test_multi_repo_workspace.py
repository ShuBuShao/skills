from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Optional


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = (
    REPOSITORY_ROOT
    / "skills"
    / "multi-repo-workspace"
    / "scripts"
    / "create_workspace.py"
)


def run(
    command: list[str], cwd: Optional[Path] = None, check: bool = True
) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        command,
        cwd=cwd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if check and completed.returncode != 0:
        raise AssertionError(
            f"command failed ({completed.returncode}): {' '.join(command)}\n"
            f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
        )
    return completed


class MultiRepoWorkspaceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def make_repository(self, name: str, with_manifest: bool = False) -> tuple[Path, Path]:
        checkout = self.root / f"{name}-source"
        remote = self.root / f"{name}.git"
        checkout.mkdir()
        run(["git", "init", "-b", "main"], cwd=checkout)
        run(["git", "config", "user.email", "test@example.com"], cwd=checkout)
        run(["git", "config", "user.name", "Skill Test"], cwd=checkout)
        (checkout / "tracked.txt").write_text("tracked remote\n", encoding="utf-8")
        if with_manifest:
            (checkout / ".worktreeinclude").write_text(
                ".env.local\n.local-tools/example-*/\ntracked.txt\nmissing.local\n",
                encoding="utf-8",
            )
        run(["git", "add", "."], cwd=checkout)
        run(["git", "commit", "-m", "initial"], cwd=checkout)
        run(["git", "switch", "-c", "feature/shared"], cwd=checkout)
        (checkout / "feature.txt").write_text("feature\n", encoding="utf-8")
        run(["git", "add", "feature.txt"], cwd=checkout)
        run(["git", "commit", "-m", "feature"], cwd=checkout)
        run(["git", "clone", "--bare", str(checkout), str(remote)])
        run(["git", "remote", "add", "origin", str(remote)], cwd=checkout)

        if with_manifest:
            (checkout / ".env.local").write_text("LOCAL_ONLY=yes\n", encoding="utf-8")
            local_tool = checkout / ".local-tools" / "example-one"
            local_tool.mkdir(parents=True)
            (local_tool / "config.json").write_text('{"local": true}\n', encoding="utf-8")
        return checkout, remote

    def write_spec(self, data: dict[str, object]) -> Path:
        path = self.root / "spec.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        return path

    def invoke(
        self, command: str, spec: Path
    ) -> tuple[subprocess.CompletedProcess[str], dict[str, object]]:
        completed = run(
            [sys.executable, str(SCRIPT), command, "--spec", str(spec)], check=False
        )
        return completed, json.loads(completed.stdout)

    def test_creates_multi_repo_workspace_and_hydrates_local_files(self) -> None:
        frontend_source, frontend_remote = self.make_repository("frontend", with_manifest=True)
        _, backend_remote = self.make_repository("backend")
        workspace = self.root / "workspace"
        spec = self.write_spec(
            {
                "workspace": str(workspace),
                "name": "feature-suite",
                "branch": "feature/shared",
                "primary": "frontend",
                "repositories": [
                    {"name": "backend", "url": str(backend_remote)},
                    {
                        "name": "frontend",
                        "local_repo": str(frontend_source),
                    },
                ],
            }
        )

        preflight, preflight_report = self.invoke("preflight", spec)
        self.assertEqual(preflight.returncode, 0)
        self.assertEqual(preflight_report["status"], "ok")
        self.assertNotIn("LOCAL_ONLY=yes", preflight.stdout)
        self.assertTrue(
            any("missing.local" in warning for warning in preflight_report["warnings"])
        )

        created, created_report = self.invoke("create", spec)
        self.assertEqual(created.returncode, 0)
        self.assertEqual(created_report["status"], "ok")
        self.assertNotIn("LOCAL_ONLY=yes", created.stdout)
        self.assertEqual(
            run(["git", "branch", "--show-current"], cwd=workspace / "frontend").stdout.strip(),
            "feature/shared",
        )
        self.assertEqual(
            run(["git", "remote", "get-url", "origin"], cwd=workspace / "frontend").stdout.strip(),
            str(frontend_remote),
        )
        self.assertEqual(
            (workspace / "frontend" / ".env.local").read_text(encoding="utf-8"),
            "LOCAL_ONLY=yes\n",
        )
        self.assertTrue(
            (workspace / "frontend" / ".local-tools" / "example-one" / "config.json").is_file()
        )
        self.assertEqual(
            (workspace / "frontend" / "tracked.txt").read_text(encoding="utf-8"),
            "tracked remote\n",
        )
        frontend_result = next(
            repository
            for repository in created_report["repositories"]
            if repository["name"] == "frontend"
        )
        self.assertIn("tracked.txt", frontend_result["hydration"]["skipped"]["paths"])
        workspace_data = json.loads(
            (workspace / "feature-suite.code-workspace").read_text(encoding="utf-8")
        )
        self.assertEqual(
            [folder["name"] for folder in workspace_data["folders"]],
            ["frontend", "backend"],
        )

    def test_creates_local_branch_from_confirmed_base(self) -> None:
        _, remote = self.make_repository("service")
        workspace = self.root / "workspace"
        spec = self.write_spec(
            {
                "workspace": str(workspace),
                "name": "new-feature",
                "branch": "feature/not-remote",
                "primary": "service",
                "repositories": [
                    {"name": "service", "url": str(remote), "base_branch": "main"}
                ],
            }
        )

        created, report = self.invoke("create", spec)
        self.assertEqual(created.returncode, 0)
        self.assertEqual(
            report["repositories"][0]["mode"], "clone-base-create-local-branch"
        )
        self.assertEqual(
            run(["git", "branch", "--show-current"], cwd=workspace / "service").stdout.strip(),
            "feature/not-remote",
        )

    def test_missing_branch_without_base_fails_without_workspace(self) -> None:
        _, remote = self.make_repository("service")
        workspace = self.root / "workspace"
        spec = self.write_spec(
            {
                "workspace": str(workspace),
                "name": "missing",
                "branch": "feature/missing",
                "primary": "service",
                "repositories": [{"name": "service", "url": str(remote)}],
            }
        )

        completed, report = self.invoke("preflight", spec)
        self.assertEqual(completed.returncode, 2)
        self.assertEqual(report["status"], "error")
        self.assertFalse(workspace.exists())

    def test_unsafe_manifest_path_is_rejected(self) -> None:
        source, remote = self.make_repository("unsafe", with_manifest=True)
        (source / ".worktreeinclude").write_text("../secret\n", encoding="utf-8")
        workspace = self.root / "workspace"
        spec = self.write_spec(
            {
                "workspace": str(workspace),
                "name": "unsafe",
                "branch": "feature/shared",
                "primary": "unsafe",
                "repositories": [
                    {"name": "unsafe", "url": str(remote), "local_repo": str(source)}
                ],
            }
        )

        completed, report = self.invoke("preflight", spec)
        self.assertEqual(completed.returncode, 2)
        self.assertEqual(report["status"], "error")
        self.assertIn("unsafe path", report["errors"][0])
        self.assertFalse(workspace.exists())

    def test_overlapping_repository_directories_are_rejected(self) -> None:
        _, remote = self.make_repository("service")
        workspace = self.root / "workspace"
        spec = self.write_spec(
            {
                "workspace": str(workspace),
                "name": "overlap",
                "branch": "feature/shared",
                "primary": "one",
                "repositories": [
                    {"name": "one", "directory": "apps", "url": str(remote)},
                    {"name": "two", "directory": "apps/two", "url": str(remote)},
                ],
            }
        )

        completed, report = self.invoke("preflight", spec)
        self.assertEqual(completed.returncode, 2)
        self.assertIn("must not overlap", report["errors"][0])
        self.assertFalse(workspace.exists())

    def test_symlink_outside_local_repository_is_rejected(self) -> None:
        source, remote = self.make_repository("symlink", with_manifest=True)
        outside = self.root / "outside-secret"
        outside.write_text("do not copy\n", encoding="utf-8")
        local_file = source / ".env.local"
        local_file.unlink()
        local_file.symlink_to(outside)
        workspace = self.root / "workspace"
        spec = self.write_spec(
            {
                "workspace": str(workspace),
                "name": "symlink",
                "branch": "feature/shared",
                "primary": "symlink",
                "repositories": [
                    {"name": "symlink", "url": str(remote), "local_repo": str(source)}
                ],
            }
        )

        completed, report = self.invoke("preflight", spec)
        self.assertEqual(completed.returncode, 2)
        self.assertIn("escapes local repository", report["errors"][0])
        self.assertFalse(workspace.exists())

    def test_invalid_directory_type_returns_structured_error(self) -> None:
        _, remote = self.make_repository("invalid")
        workspace = self.root / "workspace"
        spec = self.write_spec(
            {
                "workspace": str(workspace),
                "name": "invalid",
                "branch": "feature/shared",
                "primary": "invalid",
                "repositories": [
                    {"name": "invalid", "directory": 42, "url": str(remote)}
                ],
            }
        )

        completed, report = self.invoke("preflight", spec)
        self.assertEqual(completed.returncode, 2)
        self.assertIn("must be a non-empty string", report["errors"][0])
        self.assertEqual(completed.stderr, "")
        self.assertFalse(workspace.exists())


if __name__ == "__main__":
    unittest.main()
