"""
Tests for CI/CD automation, post-merge hooks, GitHub Actions, and container deployment isolation.
Ensures that:
- Primary container (main) deploys on port 8095 with production data volume.
- QA container (develop) deploys on port 8098 with isolated data-qa volume.
- Post-merge git hook and deploy automation scripts are executable and validate target branches.
- Data isolation is strictly enforced (.gitignore, check.sh, and docker-compose volume mounts).
"""

import os
import stat
import subprocess
import unittest
from pathlib import Path
import yaml


class TestCicdConfiguration(unittest.TestCase):
    def setUp(self):
        self.repo_root = Path(__file__).resolve().parent.parent
        self.compose_file = self.repo_root / "docker-compose.yml"
        self.deploy_script = self.repo_root / "scripts" / "deploy.sh"
        self.post_merge_hook = self.repo_root / ".githooks" / "post-merge"
        self.github_workflow = self.repo_root / ".github" / "workflows" / "cicd.yml"
        self.gitignore_file = self.repo_root / ".gitignore"
        self.check_script = self.repo_root / "scripts" / "check.sh"

    def test_docker_compose_defines_isolated_qa_service(self):
        self.assertTrue(self.compose_file.exists(), "docker-compose.yml must exist")
        with open(self.compose_file, "r", encoding="utf-8") as f:
            compose_data = yaml.safe_load(f)

        services = compose_data.get("services", {})
        self.assertIn("game-hub", services, "Primary container 'game-hub' must be defined")
        self.assertIn("game-hub-qa", services, "QA container 'game-hub-qa' must be defined")

        prod = services["game-hub"]
        qa = services["game-hub-qa"]

        # Container names
        self.assertEqual(prod.get("container_name"), "game-hub")
        self.assertEqual(qa.get("container_name"), "game-hub-qa")

        # Port separation: Prod = 8095/8097, QA = 8098/8099
        prod_ports = [str(p) for p in prod.get("ports", [])]
        qa_ports = [str(p) for p in qa.get("ports", [])]

        self.assertTrue(any("8095" in p for p in prod_ports), "Production must map port 8095")
        self.assertTrue(any("8097" in p for p in prod_ports), "Production must map port 8097")
        self.assertTrue(any("8098" in p for p in qa_ports), "QA container must map port 8098")
        self.assertTrue(any("8099" in p for p in qa_ports), "QA container must map port 8099")

        # Volume isolation: Prod uses ./data, QA uses ./data-qa
        prod_vols = [str(v) for v in prod.get("volumes", [])]
        qa_vols = [str(v) for v in qa.get("volumes", [])]

        self.assertTrue(
            any(("./data:/app/data" in v) or ("./data}:/app/data" in v) for v in prod_vols),
            "Prod must mount ./data:/app/data (or parameterized fallback ${GAMEHUB_DATA_PATH:-./data}:/app/data)",
        )
        self.assertTrue(
            any(("./data-qa:/app/data" in v) or ("./data-qa}:/app/data" in v) for v in qa_vols),
            "QA must mount ./data-qa:/app/data (or parameterized fallback ${GAMEHUB_QA_DATA_PATH:-./data-qa}:/app/data)",
        )
        self.assertFalse(any("./data:" in v or "GAMEHUB_DATA_PATH" in v for v in qa_vols), "QA MUST NEVER mount production ./data")

    def test_deploy_script_exists_and_is_executable(self):
        self.assertTrue(self.deploy_script.exists(), "scripts/deploy.sh must exist")
        st = os.stat(self.deploy_script)
        self.assertTrue(bool(st.st_mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)), "deploy.sh must be executable")

        with open(self.deploy_script, "r", encoding="utf-8") as f:
            content = f.read()

        # Must support main and develop/qa targets
        self.assertIn("main", content)
        self.assertIn("develop", content)
        self.assertIn("game-hub", content)
        self.assertIn("game-hub-qa", content)
        self.assertIn("8095", content)
        self.assertIn("8098", content)
        # Must enforce backup on production deploy
        self.assertIn(".bak", content)

    def test_post_merge_hook_exists_and_is_executable(self):
        self.assertTrue(self.post_merge_hook.exists(), ".githooks/post-merge must exist")
        st = os.stat(self.post_merge_hook)
        self.assertTrue(bool(st.st_mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)), ".githooks/post-merge must be executable")

        with open(self.post_merge_hook, "r", encoding="utf-8") as f:
            content = f.read()

        self.assertIn("deploy.sh", content)
        self.assertIn("main", content)
        self.assertIn("develop", content)

    def test_github_workflow_valid_and_targets_branches(self):
        self.assertTrue(self.github_workflow.exists(), ".github/workflows/cicd.yml must exist")
        with open(self.github_workflow, "r", encoding="utf-8") as f:
            workflow = yaml.safe_load(f)

        # PyYAML may parse 'on' as boolean True or string 'on'
        triggers = workflow.get("on") or workflow.get(True) or {}
        self.assertTrue("push" in triggers or "pull_request" in triggers)

        # Check branches in push or pull_request
        branches = []
        if "push" in triggers and isinstance(triggers["push"], dict):
            branches.extend(triggers["push"].get("branches", []))
        if "pull_request" in triggers and isinstance(triggers["pull_request"], dict):
            branches.extend(triggers["pull_request"].get("branches", []))

        self.assertIn("main", branches)
        self.assertIn("develop", branches)

    def test_data_qa_ignored_and_protected_in_quality_gate(self):
        self.assertTrue(self.gitignore_file.exists())
        with open(self.gitignore_file, "r", encoding="utf-8") as f:
            gitignore = f.read()
        self.assertIn("data-qa", gitignore, ".gitignore must ignore data-qa/")

        with open(self.check_script, "r", encoding="utf-8") as f:
            check_content = f.read()
        self.assertIn("data-qa", check_content, "scripts/check.sh must guard against data-qa leaks")

    def test_deploy_supports_configurable_hdd_storage_and_pruning(self):
        with open(self.deploy_script, "r", encoding="utf-8") as f:
            content = f.read()

        self.assertIn("GAMEHUB_DATA_PATH", content)
        self.assertIn("GAMEHUB_QA_DATA_PATH", content)
        self.assertIn("GAMEHUB_BACKUP_PATH", content)
        self.assertIn("prune_backups", content)

    def test_backup_pruning_retains_latest_n_files(self):
        import tempfile
        import time

        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            # Create 20 mock backup files with spaced timestamps
            for i in range(20):
                f = tmppath / f"games.db.bak-20261101-{i:06d}"
                f.write_text("backup")
                # Set distinct modification times
                mtime = time.time() - (20 - i) * 60
                os.utime(f, (mtime, mtime))

            # Run the pruning snippet
            cmd = f"""
            prune_backups() {{
              local dir="$1"
              local keep="${{2:-15}}"
              if [[ -d "$dir" ]]; then
                find "$dir" -maxdepth 1 -type f \\( -name "*.bak-*" -o -name "*.bak" \\) -printf '%T@ %p\\n' 2>/dev/null | \\
                  sort -nr | tail -n +$((keep + 1)) | cut -d' ' -f2- | while IFS= read -r old_file; do
                    if [[ -f "$old_file" ]]; then
                      rm -f "$old_file"
                    fi
                  done
              fi
            }}
            prune_backups "{tmppath}" 15
            """
            result = subprocess.run(["bash", "-c", cmd], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0)

            remaining = list(tmppath.glob("*.bak-*"))
            self.assertEqual(len(remaining), 15, "Should prune older backups and keep exactly 15 newest")
            # Ensure the oldest 5 were deleted
            for i in range(5):
                self.assertFalse((tmppath / f"games.db.bak-20261101-{i:06d}").exists())
            # Ensure the newest 15 exist
            for i in range(5, 20):
                self.assertTrue((tmppath / f"games.db.bak-20261101-{i:06d}").exists())


if __name__ == "__main__":
    unittest.main()
