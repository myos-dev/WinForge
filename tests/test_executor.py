"""Tests for the WinForge container executor and build-script generator."""
from __future__ import annotations
import json, os, stat, tempfile, unittest
from pathlib import Path

from builder.pipeline import build_plan, generate_build_script
from builder.executor import (
    BuildResult,
    _check_image,
    _find_engine,
    _pull_image,
    _resolve_image_ref,
    execute_inside_container,
)
from core.manifest import load_manifest


MANIFEST_JSON = json.dumps({
    "schemaVersion": "winforge.dev/v0",
    "name": "test-app",
    "version": "0.1.0",
    "runtime": {"provider": "wine", "version": "9.0"},
    "dependencies": [
        {"kind": "winetricks", "verbs": ["corefonts", "vcrun2022"]},
    ],
    "install": [
        {"kind": "portable", "source": "file://./sources/app.zip",
         "target": "C:/Program Files/TestApp"},
    ],
    "filesystem": [
        {"source": "./overlays/config.xml",
         "target": "C:/Program Files/TestApp/config.xml"},
    ],
    "launch": {
        "entrypoint": "C:/Program Files/TestApp/app.exe",
        "args": [],
        "env": {},
        "workingDirectory": "C:/Program Files/TestApp",
    },
})


class BuildScriptGenerationTests(unittest.TestCase):
    """The build script is generated from a manifest and contains the
    correct phase commands for execution inside the Wine container."""

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp(prefix="winforge_test_"))
        manifest_path = self.tmpdir / "manifest.json"
        manifest_path.write_text(MANIFEST_JSON, encoding="utf-8")
        self.manifest = load_manifest(manifest_path)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_generate_script_includes_shebang(self):
        script = generate_build_script(self.manifest)
        self.assertTrue(script.startswith("#!/bin/bash"),
                        "Script must have bash shebang")
        self.assertIn("set -euo pipefail", script)

    def test_generate_script_includes_all_phases(self):
        script = generate_build_script(self.manifest)
        for phase_name in ("init-prefix", "install-dependencies",
                           "install-apps", "apply-layout-and-registry",
                           "validate", "seal-artifact"):
            self.assertIn(phase_name, script,
                          f"Phase {phase_name} must appear in script")

    def test_generate_script_has_wineboot(self):
        script = generate_build_script(self.manifest)
        self.assertIn("wineboot --init", script)

    def test_generate_script_has_winetricks_verbs(self):
        script = generate_build_script(self.manifest)
        self.assertIn("corefonts", script)
        self.assertIn("vcrun2022", script)
        self.assertIn("winetricks -q", script)

    def test_generate_script_has_filesystem_mapping(self):
        script = generate_build_script(self.manifest)
        self.assertIn("config.xml", script)
        self.assertIn("cp -r", script)

    def test_generate_script_has_portable_extraction(self):
        script = generate_build_script(self.manifest)
        self.assertIn("unzip -o", script)
        self.assertIn("app.zip", script)

    def test_generate_script_has_validation(self):
        script = generate_build_script(self.manifest)
        self.assertIn("Entrypoint exists", script)
        self.assertIn("prefix-filelist.txt", script)

    def test_generate_script_has_build_result_marker(self):
        script = generate_build_script(self.manifest)
        self.assertIn("BUILD COMPLETE", script)
        self.assertIn('"build": "complete"', script)

    def test_generate_script_executable_structure(self):
        """The script should be valid bash syntax by basic check."""
        script = generate_build_script(self.manifest)
        # Count only bash keyword 'fi' at line start (ignores substrings
        # in words like 'prefix', 'FileCount')
        fi_lines = sum(1 for line in script.split('\n')
                       if line.strip().startswith('fi'))
        if_lines = sum(1 for line in script.split('\n')
                       if line.strip().startswith('if ') or
                       line.strip().startswith('if\t') or
                       line.strip() == 'if')
        self.assertGreaterEqual(if_lines, 1,
                            "Script should have at least one if block")
        self.assertGreaterEqual(fi_lines, 1,
                            "Script should have at least one fi")
        self.assertGreater(script.count('echo'), 10,
                           "Script should have many echo statements")


class BuildResultTests(unittest.TestCase):
    """BuildResult dataclass serialization and construction."""

    def test_success_result(self):
        r = BuildResult(
            success=True,
            bundle_path="/tmp/test",
            runtime_provider="wine",
            runtime_version="9.0",
            image_ref="winforge/wine:9.0",
            engine="docker",
            exit_code=0,
            log="build complete",
            prefix_size=1048576,
            prefix_file_count=42,
        )
        d = r.to_dict()
        self.assertTrue(d["success"])
        self.assertEqual(d["prefixSize"], 1048576)
        self.assertEqual(d["prefixFileCount"], 42)
        self.assertEqual(d["exitCode"], 0)

    def test_failure_result(self):
        r = BuildResult(
            success=False,
            bundle_path="/tmp/test",
            runtime_provider="wine",
            runtime_version="9.0",
            image_ref="",
            engine="docker",
            error="Engine not found",
        )
        d = r.to_dict()
        self.assertFalse(d["success"])
        self.assertEqual(d["error"], "Engine not found")

    def test_result_with_none_fields(self):
        r = BuildResult(
            success=True,
            bundle_path="/tmp/test",
            runtime_provider="wine",
            runtime_version="9.0",
            image_ref="winforge/wine:9.0",
            engine="docker",
        )
        d = r.to_dict()
        self.assertIsNone(d["exitCode"])
        self.assertIsNone(d["prefixSize"])


class EngineDetectionTests(unittest.TestCase):
    """Container engine auto-detection."""

    def test_find_engine_prefer_docker(self):
        """At the very least, 'docker' or 'podman' should be in PATH or
        _find_engine raises RuntimeError with a helpful message."""
        try:
            engine = _find_engine()
            self.assertIn(engine, ("docker", "podman"))
        except RuntimeError as e:
            self.assertIn("Install Docker or Podman", str(e))

    def test_image_check_no_hang_on_bogus_ref(self):
        """_check_image should return False for made-up refs, not hang."""
        try:
            result = _check_image("winforge/nonexistent:999.999", "docker")
            self.assertFalse(result)
        except FileNotFoundError:
            pass  # Docker not installed — acceptable

    def test_pull_bogus_image(self):
        """_pull_image should return False for non-existent images."""
        try:
            result = _pull_image("winforge/nonexistent:999.999", "docker")
            self.assertFalse(result)
        except FileNotFoundError:
            pass  # Docker not installed — acceptable


class BuildPlanTests(unittest.TestCase):
    """build_plan produces the correct phase structure."""

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp(prefix="winforge_test_"))
        manifest_path = self.tmpdir / "manifest.json"
        manifest_path.write_text(MANIFEST_JSON, encoding="utf-8")
        self.manifest = load_manifest(manifest_path)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_plan_has_6_phases(self):
        plan = build_plan(self.manifest)
        self.assertEqual(len(plan), 6)

    def test_plan_each_phase_has_phase_action_inputs_keys(self):
        plan = build_plan(self.manifest)
        for phase in plan:
            for key in ("phase", "inputs", "actions"):
                self.assertIn(key, phase, f"Phase missing key '{key}': {phase}")

    def test_plan_contains_winetricks_verbs(self):
        plan = build_plan(self.manifest)
        dep_phase = plan[1]  # install-dependencies
        actions_str = " ".join(str(a) for a in dep_phase["actions"])
        self.assertIn("corefonts", actions_str)
        self.assertIn("vcrun2022", actions_str)

    def test_plan_phase_order_is_correct(self):
        plan = build_plan(self.manifest)
        expected = [
            "init-prefix", "install-dependencies", "install-apps",
            "apply-layout-and-registry", "validate", "seal-artifact",
        ]
        actual = [p["phase"] for p in plan]
        self.assertEqual(actual, expected)


class BuildScriptNoDepsTests(unittest.TestCase):
    """Script generation with an empty manifest (no deps, no installs)."""

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp(prefix="winforge_test_"))
        data = {
            "schemaVersion": "winforge.dev/v0",
            "name": "empty-app",
            "version": "0.0.1",
            "runtime": {"provider": "wine", "version": "9.0"},
            "dependencies": [],
            "install": [],
            "filesystem": [],
            "launch": {"entrypoint": "C:/app.exe", "args": [],
                       "env": {}, "workingDirectory": "C:/"},
        }
        manifest_path = self.tmpdir / "manifest.json"
        manifest_path.write_text(json.dumps(data), encoding="utf-8")
        self.manifest = load_manifest(manifest_path)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_empty_deps_skips_winetricks(self):
        script = generate_build_script(self.manifest)
        self.assertIn("No dependencies declared", script)
        self.assertNotIn("winetricks", script)

    def test_empty_install_skips_installers(self):
        script = generate_build_script(self.manifest)
        self.assertIn("No application install steps declared", script)

    def test_empty_filesystem_skips_mappings(self):
        script = generate_build_script(self.manifest)
        self.assertIn("No filesystem mappings declared", script)

    def test_script_still_has_6_phases(self):
        script = generate_build_script(self.manifest)
        for phase in ("init-prefix", "install-dependencies",
                      "install-apps", "apply-layout-and-registry",
                      "validate", "seal-artifact"):
            self.assertIn(phase, script)


if __name__ == "__main__":
    unittest.main()
