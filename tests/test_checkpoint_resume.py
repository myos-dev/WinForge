from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from artifact.bundle import create_bundle
from artifact.checkpoint import (
    CHECKPOINT_RESUME_SCHEMA_VERSION,
    CHECKPOINT_SCHEMA_VERSION,
    CheckpointError,
    inspect_checkpoint,
    resume_checkpoint,
    seed_bundle_from_checkpoint,
)
from builder.executor import BuildResult
from builder.pipeline import generate_build_script
from compat.evidence import run_compat_test
from core.manifest import Manifest


VALID = {
    "schemaVersion": "winforge.dev/v0",
    "name": "checkpoint-demo",
    "version": "1.0.0",
    "runtime": {"provider": "wine", "version": "9.0"},
    "dependencies": [{"kind": "winetricks", "verbs": ["corefonts"]}],
    "install": [{
        "kind": "portable",
        "source": "file://app.zip",
        "target": "C:/Program Files/App",
    }],
    "filesystem": [],
    "launch": {
        "entrypoint": "C:/Program Files/App/App.exe",
        "args": [],
        "env": {},
        "workingDirectory": "C:/Program Files/App",
    },
    "provenance": {"sources": []},
}


def _manifest() -> Manifest:
    return Manifest.from_dict(VALID)


def _write_manifest(path: Path, payload: dict | None = None) -> Path:
    payload = dict(payload or VALID)
    (path.parent / "app.zip").write_bytes(b"fake portable app archive\n")
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


class CheckpointInspectionTests(unittest.TestCase):
    def _bundle_under_parent(self, parent: Path) -> Path:
        bundle = create_bundle(_manifest(), parent, dry_run=True)
        (bundle / "prefix" / "drive_c" / "prepared.txt").write_text("prepared\n", encoding="utf-8")
        return bundle

    def test_inspect_checkpoint_locates_nested_bundle_from_output_parent(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            parent = root / "compat-output"
            parent.mkdir()
            bundle = self._bundle_under_parent(parent)

            result = inspect_checkpoint(parent)

        self.assertEqual(result["schemaVersion"], CHECKPOINT_SCHEMA_VERSION)
        self.assertTrue(result["valid"])
        self.assertEqual(result["inputKind"], "output-parent")
        self.assertEqual(result["bundle"], str(bundle.resolve()))
        self.assertEqual(result["files"]["prefix/drive_c"]["type"], "directory")
        self.assertEqual(result["application"], {"name": "checkpoint-demo", "version": "1.0.0"})

    def test_inspect_checkpoint_rejects_symlinked_checkpoint_input_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            real_parent = root / "real-output"
            real_parent.mkdir()
            self._bundle_under_parent(real_parent)
            linked_parent = root / "linked-output"
            linked_parent.symlink_to(real_parent, target_is_directory=True)

            result = inspect_checkpoint(linked_parent)

        self.assertFalse(result["valid"])
        self.assertTrue(any("symlink" in error for error in result["errors"]))
        self.assertEqual(result["bundle"], None)

    def test_inspect_checkpoint_rejects_parent_without_valid_bundle(self):
        with tempfile.TemporaryDirectory() as tmp:
            parent = Path(tmp) / "empty-output"
            parent.mkdir()
            result = inspect_checkpoint(parent)

        self.assertFalse(result["valid"])
        self.assertIn("no valid checkpoint bundle found", result["errors"])
        self.assertEqual(result["candidates"], [])

    def test_inspect_checkpoint_rejects_bundle_missing_prefix(self):
        with tempfile.TemporaryDirectory() as tmp:
            bundle = self._bundle_under_parent(Path(tmp))
            (bundle / "prefix" / "drive_c" / ".keep").unlink(missing_ok=True)
            (bundle / "prefix" / "drive_c" / "prepared.txt").unlink()
            (bundle / "prefix" / "drive_c").rmdir()

            result = inspect_checkpoint(bundle)

        self.assertFalse(result["valid"])
        self.assertIn("missing required checkpoint file: prefix/drive_c", result["errors"])

    def test_inspect_checkpoint_rejects_symlinked_required_json_without_parsing_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bundle = self._bundle_under_parent(root)
            outside = root / "outside-manifest.json"
            outside.write_text(json.dumps({"name": "secret-outside", "version": "9.9.9"}), encoding="utf-8")
            (bundle / "manifest.winforge.json").unlink()
            (bundle / "manifest.winforge.json").symlink_to(outside)

            result = inspect_checkpoint(bundle)

        self.assertFalse(result["valid"])
        self.assertIn("checkpoint manifest.winforge.json must not be a symlink", result["errors"])
        self.assertNotEqual(result["application"].get("name"), "secret-outside")
        self.assertEqual(result["application"], {"name": None, "version": None})

    def test_inspect_checkpoint_rejects_symlinked_top_level_bundle_roots(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bundle = self._bundle_under_parent(root)
            outside = root / "outside-launch"
            outside.mkdir()
            (bundle / "launch" / "entrypoint.json").unlink()
            (bundle / "launch").rmdir()
            (bundle / "launch").symlink_to(outside, target_is_directory=True)

            result = inspect_checkpoint(bundle)

        self.assertFalse(result["valid"])
        self.assertIn("checkpoint launch must not be a symlink", result["errors"])

    def test_inspect_checkpoint_rejects_symlinked_structural_prefix_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bundle = self._bundle_under_parent(root)
            outside = root / "outside-drive-c"
            outside.mkdir()
            (bundle / "prefix" / "drive_c" / ".keep").unlink(missing_ok=True)
            (bundle / "prefix" / "drive_c" / "prepared.txt").unlink()
            (bundle / "prefix" / "drive_c").rmdir()
            (bundle / "prefix" / "drive_c").symlink_to(outside, target_is_directory=True)

            result = inspect_checkpoint(bundle)

        self.assertFalse(result["valid"])
        self.assertIn("checkpoint prefix/drive_c must not be a symlink", result["errors"])

    def test_resume_checkpoint_copies_to_fresh_attempt_without_mutating_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source_parent = root / "compat-output"
            source_parent.mkdir()
            source_bundle = self._bundle_under_parent(source_parent)
            attempts = root / "attempts"

            result = resume_checkpoint(source_parent, output_dir=attempts, name="manual-attempt")
            attempt = Path(result["attemptBundle"])
            (attempt / "prefix" / "drive_c" / "attempt-only.txt").write_text("changed\n", encoding="utf-8")

            source_attempt_file = source_bundle / "prefix" / "drive_c" / "attempt-only.txt"
            metadata = json.loads((attempt / "metadata" / "checkpoint-resume.json").read_text(encoding="utf-8"))

        self.assertEqual(result["schemaVersion"], CHECKPOINT_RESUME_SCHEMA_VERSION)
        self.assertEqual(result["sourceBundle"], str(source_bundle.resolve()))
        self.assertEqual(result["attemptBundle"], str(attempt.resolve()))
        self.assertFalse(source_attempt_file.exists())
        self.assertEqual(metadata["sourceBundle"], str(source_bundle.resolve()))
        self.assertEqual(metadata["attemptBundle"], str(attempt.resolve()))

    def test_resume_checkpoint_rejects_existing_attempt_without_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source_parent = root / "compat-output"
            source_parent.mkdir()
            self._bundle_under_parent(source_parent)
            attempts = root / "attempts"
            resume_checkpoint(source_parent, output_dir=attempts, name="manual-attempt")

            with self.assertRaises(CheckpointError):
                resume_checkpoint(source_parent, output_dir=attempts, name="manual-attempt")

    def test_resume_checkpoint_rejects_dot_and_dotdot_attempt_names(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source_parent = root / "compat-output"
            source_parent.mkdir()
            self._bundle_under_parent(source_parent)
            attempts = root / "attempts"

            for bad_name in [".", ".."]:
                with self.subTest(bad_name=bad_name):
                    with self.assertRaises(CheckpointError):
                        resume_checkpoint(source_parent, output_dir=attempts, name=bad_name, overwrite=True)

    def test_resume_checkpoint_rejects_existing_non_directory_attempt_on_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source_parent = root / "compat-output"
            source_parent.mkdir()
            self._bundle_under_parent(source_parent)
            attempts = root / "attempts"
            attempts.mkdir()
            (attempts / "manual-attempt").write_text("not a directory\n", encoding="utf-8")

            with self.assertRaises(CheckpointError):
                resume_checkpoint(source_parent, output_dir=attempts, name="manual-attempt", overwrite=True)

    def test_resume_checkpoint_rejects_non_directory_output_ancestor_before_mkdir(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source_parent = root / "compat-output"
            source_parent.mkdir()
            self._bundle_under_parent(source_parent)
            bad_parent = root / "not-a-directory"
            bad_parent.write_text("file\n", encoding="utf-8")

            with self.assertRaises(CheckpointError):
                resume_checkpoint(source_parent, output_dir=bad_parent / "child", name="attempt")

            self.assertEqual(bad_parent.read_text(encoding="utf-8"), "file\n")

    def test_resume_checkpoint_rejects_symlinked_output_directory_before_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source_parent = root / "compat-output"
            source_parent.mkdir()
            self._bundle_under_parent(source_parent)
            real_output = root / "real-output"
            real_output.mkdir()
            linked_output = root / "linked-output"
            linked_output.symlink_to(real_output, target_is_directory=True)

            with self.assertRaises(CheckpointError):
                resume_checkpoint(source_parent, output_dir=linked_output, name="attempt", overwrite=True)

            self.assertEqual(list(real_output.iterdir()), [])

    def test_resume_checkpoint_rejects_output_inside_source_without_creating_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source_parent = root / "compat-output"
            source_parent.mkdir()
            source_bundle = self._bundle_under_parent(source_parent)
            nested_output = source_bundle / "nested-attempts"

            with self.assertRaises(CheckpointError):
                resume_checkpoint(source_parent, output_dir=nested_output, name="attempt")

            self.assertFalse(nested_output.exists())

    def test_resume_checkpoint_rejects_symlinked_attempt_root_before_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source_parent = root / "compat-output"
            source_parent.mkdir()
            self._bundle_under_parent(source_parent)
            attempts = root / "attempts"
            attempts.mkdir()
            victim = attempts / "victim"
            victim.mkdir()
            (victim / "keep.txt").write_text("keep\n", encoding="utf-8")
            (attempts / "manual-attempt").symlink_to(victim, target_is_directory=True)

            with self.assertRaises(CheckpointError):
                resume_checkpoint(source_parent, output_dir=attempts, name="manual-attempt", overwrite=True)

            self.assertTrue((victim / "keep.txt").exists())
            self.assertTrue((attempts / "manual-attempt").is_symlink())

    def test_resume_checkpoint_rejects_metadata_temp_directory_and_cleans_fresh_attempt(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source_parent = root / "compat-output"
            source_parent.mkdir()
            source_bundle = self._bundle_under_parent(source_parent)
            (source_bundle / "metadata" / "checkpoint-resume.json.tmp").mkdir()
            attempts = root / "attempts"

            with self.assertRaises(CheckpointError):
                resume_checkpoint(source_parent, output_dir=attempts, name="manual-attempt")

            self.assertFalse((attempts / "manual-attempt").exists())

    def test_resume_checkpoint_rejects_copied_metadata_temp_symlink(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source_parent = root / "compat-output"
            source_parent.mkdir()
            source_bundle = self._bundle_under_parent(source_parent)
            outside = root / "outside-write.txt"
            (source_bundle / "metadata" / "checkpoint-resume.json.tmp").symlink_to(outside)

            with self.assertRaises(CheckpointError):
                resume_checkpoint(source_parent, output_dir=root / "attempts", name="manual-attempt")

            self.assertFalse(outside.exists())

    def test_resume_checkpoint_preserves_legitimate_wine_dosdevices_symlinks(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source_parent = root / "compat-output"
            source_parent.mkdir()
            self._bundle_under_parent(source_parent)
            source_bundle = inspect_checkpoint(source_parent)["bundle"]
            dosdevices = Path(source_bundle) / "prefix" / "dosdevices"
            dosdevices.mkdir()
            (dosdevices / "c:").symlink_to("../drive_c")
            (dosdevices / "z:").symlink_to("/")

            result = resume_checkpoint(source_parent, output_dir=root / "attempts", name="wine-prefix-attempt")
            attempt_dosdevices = Path(result["attemptBundle"]) / "prefix" / "dosdevices"

            self.assertTrue((attempt_dosdevices / "c:").is_symlink())
            self.assertEqual((attempt_dosdevices / "c:").readlink().as_posix(), "../drive_c")
            self.assertTrue((attempt_dosdevices / "z:").is_symlink())
            self.assertEqual((attempt_dosdevices / "z:").readlink().as_posix(), "/")

    def test_seed_bundle_from_checkpoint_replaces_only_attempt_prefix_and_records_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source_parent = root / "compat-output"
            source_parent.mkdir()
            source_bundle = self._bundle_under_parent(source_parent)
            attempt_bundle = create_bundle(_manifest(), root / "attempt-output", dry_run=True)
            (attempt_bundle / "manifest.winforge.json").write_text(
                json.dumps({**VALID, "name": "attempt-manifest"}, indent=2),
                encoding="utf-8",
            )

            result = seed_bundle_from_checkpoint(source_parent, attempt_bundle)
            (attempt_bundle / "prefix" / "drive_c" / "attempt-only.txt").write_text("changed\n", encoding="utf-8")
            manifest_payload = json.loads((attempt_bundle / "manifest.winforge.json").read_text(encoding="utf-8"))
            self.assertEqual(result["sourceBundle"], str(source_bundle.resolve()))
            self.assertEqual(result["attemptBundle"], str(attempt_bundle.resolve()))
            self.assertTrue((attempt_bundle / "prefix" / "drive_c" / "prepared.txt").exists())
            self.assertFalse((source_bundle / "prefix" / "drive_c" / "attempt-only.txt").exists())
            self.assertEqual(manifest_payload["name"], "attempt-manifest")

    def test_seed_bundle_from_checkpoint_rejects_symlinked_attempt_bundle_parent(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source_parent = root / "compat-output"
            source_parent.mkdir()
            self._bundle_under_parent(source_parent)
            real_parent = root / "real-attempt-parent"
            real_parent.mkdir()
            real_attempt = create_bundle(_manifest(), real_parent, dry_run=True)
            linked_parent = root / "linked-attempt-parent"
            linked_parent.symlink_to(real_parent, target_is_directory=True)
            linked_attempt = linked_parent / real_attempt.name

            with self.assertRaises(CheckpointError):
                seed_bundle_from_checkpoint(source_parent, linked_attempt)

    def test_seed_bundle_from_checkpoint_rejects_symlinked_attempt_bundle_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source_parent = root / "compat-output"
            source_parent.mkdir()
            self._bundle_under_parent(source_parent)
            real_attempt = create_bundle(_manifest(), root / "attempt-output", dry_run=True)
            linked_attempt = root / "linked-attempt"
            linked_attempt.symlink_to(real_attempt, target_is_directory=True)

            with self.assertRaises(CheckpointError):
                seed_bundle_from_checkpoint(source_parent, linked_attempt)

    def test_cli_debug_checkpoint_inspect_and_resume(self):
        repo = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source_parent = root / "compat-output"
            source_parent.mkdir()
            self._bundle_under_parent(source_parent)
            inspect_proc = subprocess.run(
                [sys.executable, "cmd/winforge.py", "debug", "checkpoint", "inspect", str(source_parent)],
                cwd=repo,
                text=True,
                capture_output=True,
                check=False,
            )
            resume_proc = subprocess.run(
                [
                    sys.executable,
                    "cmd/winforge.py",
                    "debug",
                    "checkpoint",
                    "resume",
                    str(source_parent),
                    "--output",
                    str(root / "attempts"),
                    "--name",
                    "cli-attempt",
                ],
                cwd=repo,
                text=True,
                capture_output=True,
                check=False,
            )

        self.assertEqual(inspect_proc.returncode, 0, inspect_proc.stderr)
        self.assertTrue(json.loads(inspect_proc.stdout)["valid"])
        self.assertEqual(resume_proc.returncode, 0, resume_proc.stderr)
        self.assertEqual(json.loads(resume_proc.stdout)["name"], "cli-attempt")


class CheckpointCompatIntegrationTests(unittest.TestCase):
    def test_compat_test_resume_from_bundle_seeds_fresh_bundle_and_records_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            checkpoint_parent = root / "checkpoint-output"
            checkpoint_parent.mkdir()
            checkpoint_bundle = create_bundle(_manifest(), checkpoint_parent, dry_run=True)
            (checkpoint_bundle / "prefix" / "drive_c" / "prepared.txt").write_text("prepared\n", encoding="utf-8")
            manifest_path = _write_manifest(root / "recipe.winforge.json")

            def fake_build(manifest, bundle_path, *, engine, image_ref, timeout, workspace, runner_cache_dir=None, stop_before=None):
                self.assertTrue((bundle_path / "prefix" / "drive_c" / "prepared.txt").exists())
                (bundle_path / "prefix" / "drive_c" / "attempt-only.txt").write_text("mutated\n", encoding="utf-8")
                return BuildResult(
                    success=True,
                    bundle_path=str(bundle_path),
                    runtime_provider=manifest.runtime.provider,
                    runtime_version=manifest.runtime.version,
                    image_ref=image_ref,
                    engine=engine,
                    exit_code=0,
                )

            with patch("compat.evidence.execute_inside_container", side_effect=fake_build):
                result = run_compat_test(
                    manifest_path,
                    output_dir=root / "attempt-output",
                    workspace=root,
                    graphics="headless",
                    engine="docker",
                    mode="build",
                    resume_from_bundle=checkpoint_parent,
                )

            attempt_bundle = Path(result["build"]["bundle"])
            self.assertTrue(result["success"])
            self.assertEqual(result["checkpoint"]["sourceBundle"], str(checkpoint_bundle.resolve()))
            self.assertEqual(result["checkpoint"]["attemptBundle"], str(attempt_bundle.resolve()))
            self.assertTrue((attempt_bundle / "prefix" / "drive_c" / "attempt-only.txt").exists())
            self.assertFalse((checkpoint_bundle / "prefix" / "drive_c" / "attempt-only.txt").exists())

    def test_compat_test_rejects_symlinked_output_directory_before_creating_attempt(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            checkpoint_parent = root / "checkpoint-output"
            checkpoint_parent.mkdir()
            checkpoint_bundle = create_bundle(_manifest(), checkpoint_parent, dry_run=True)
            real_output = root / "real-output"
            real_output.mkdir()
            linked_output = root / "linked-output"
            linked_output.symlink_to(real_output, target_is_directory=True)
            manifest_path = _write_manifest(root / "recipe.winforge.json", {**VALID, "version": "2.0.0"})

            result = run_compat_test(
                manifest_path,
                output_dir=linked_output,
                workspace=root,
                graphics="headless",
                engine="docker",
                mode="build",
                resume_from_bundle=checkpoint_bundle,
            )

            self.assertFalse(result["success"])
            self.assertEqual(result["classification"], "harness-error")
            self.assertIn("output path must not contain symlink components", result["error"])
            self.assertEqual(list(real_output.iterdir()), [])

    def test_compat_test_rejects_output_inside_checkpoint_before_creating_attempt(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            checkpoint_parent = root / "checkpoint-output"
            checkpoint_parent.mkdir()
            checkpoint_bundle = create_bundle(_manifest(), checkpoint_parent, dry_run=True)
            manifest_path = _write_manifest(root / "recipe.winforge.json", {**VALID, "version": "2.0.0"})
            nested_output = checkpoint_bundle / "nested-output"

            result = run_compat_test(
                manifest_path,
                output_dir=nested_output,
                workspace=root,
                graphics="headless",
                engine="docker",
                mode="build",
                resume_from_bundle=checkpoint_bundle,
            )

            self.assertFalse(result["success"])
            self.assertEqual(result["classification"], "harness-error")
            self.assertIn("inside the resume checkpoint", result["error"])
            self.assertFalse(nested_output.exists())

    def test_compat_test_resolves_resume_output_parent_before_creating_attempt(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            checkpoint_parent = root / "checkpoint-output"
            checkpoint_parent.mkdir()
            checkpoint_bundle = create_bundle(_manifest(), checkpoint_parent, dry_run=True)
            (checkpoint_bundle / "prefix" / "drive_c" / "prepared.txt").write_text("prepared\n", encoding="utf-8")
            attempt_payload = {**VALID, "version": "2.0.0"}
            manifest_path = _write_manifest(root / "recipe.winforge.json", attempt_payload)

            def fake_build(manifest, bundle_path, *, engine, image_ref, timeout, workspace, runner_cache_dir=None, stop_before=None):
                self.assertTrue((bundle_path / "prefix" / "drive_c" / "prepared.txt").exists())
                return BuildResult(
                    success=True,
                    bundle_path=str(bundle_path),
                    runtime_provider=manifest.runtime.provider,
                    runtime_version=manifest.runtime.version,
                    image_ref=image_ref,
                    engine=engine,
                    exit_code=0,
                )

            with patch("compat.evidence.execute_inside_container", side_effect=fake_build):
                result = run_compat_test(
                    manifest_path,
                    output_dir=checkpoint_parent,
                    workspace=root,
                    graphics="headless",
                    engine="docker",
                    mode="build",
                    resume_from_bundle=checkpoint_parent,
                )

            self.assertTrue(result["success"])
            self.assertEqual(result["checkpoint"]["sourceBundle"], str(checkpoint_bundle.resolve()))
            self.assertEqual(Path(result["build"]["bundle"]).parent, checkpoint_parent)

    def test_generate_build_script_can_stop_before_install_apps_for_checkpoint_prep(self):
        script = generate_build_script(_manifest(), stop_before="install-apps")

        self.assertIn("Stop requested before phase: install-apps", script)
        self.assertNotIn("Phase 4/6: Installing applications", script)
        self.assertIn('"stoppedBefore": "install-apps"', script)
        self.assertNotIn("ENDMARKER", script)
        self.assertNotIn("cat >", script)
        self.assertIn('prefix_size=$(du -sb "$WINEPREFIX"', script)
        self.assertIn("printf", script)
        self.assertIn('"prefixSize": %s', script)
        self.assertIn('"buildTimestamp": %s', script)

    def test_stop_before_build_result_does_not_shell_expand_manifest_strings(self):
        payload = dict(VALID)
        payload["name"] = "danger-$(touch /tmp/winforge-pwned)-`echo bad`"
        manifest = Manifest.from_dict(payload)

        script = generate_build_script(manifest, stop_before="install-apps")

        self.assertIn("'\"danger-$(touch /tmp/winforge-pwned)-`echo bad`\"'", script)
        self.assertNotIn('"manifestName": "danger-$(touch /tmp/winforge-pwned)-`echo bad`"', script)

    def test_cli_rejects_stop_before_with_run_mode_without_traceback(self):
        repo = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest_path = _write_manifest(root / "recipe.winforge.json")
            proc = subprocess.run(
                [
                    sys.executable,
                    "cmd/winforge.py",
                    "compat",
                    "test",
                    str(manifest_path),
                    "--workspace",
                    str(root),
                    "--output",
                    str(root / "dist"),
                    "--mode",
                    "run",
                    "--stop-before",
                    "install-apps",
                ],
                cwd=repo,
                text=True,
                capture_output=True,
                check=False,
            )

        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("--stop-before is only supported with --mode dry-run or --mode build", proc.stderr)
        self.assertNotIn("Traceback", proc.stderr)

    def test_compat_test_rejects_stop_before_install_apps_with_run_mode(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest_path = _write_manifest(root / "recipe.winforge.json")

            with self.assertRaises(ValueError):
                run_compat_test(
                    manifest_path,
                    output_dir=root / "dist",
                    workspace=root,
                    mode="run",
                    stop_before="install-apps",
                )


if __name__ == "__main__":
    unittest.main()
