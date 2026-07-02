from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from builder.pipeline import generate_build_script
from core.manifest import Manifest, ManifestError, load_manifest


def _module_manifest() -> dict[str, object]:
    return {
        "schemaVersion": "winforge.app/v0",
        "name": "choco-demo",
        "version": "1.0.0",
        "runtime": {"provider": "wine", "version": "latest"},
        "modules": [
            {
                "type": "chocolatey",
                "install": {
                    "packages": ["firefox", "7zip.install"],
                },
            }
        ],
        "launch": {"entrypoint": "C:/Program Files/Mozilla Firefox/firefox.exe"},
    }



class ChocolateyModuleUnitTests(unittest.TestCase):
    """Standalone unit tests for module expansion not mediated by Manifest.from_dict."""

    def test_apply_modules_empty(self):
        from core.modules import apply_modules
        result = apply_modules({"schemaVersion": "winforge.app/v0"})
        self.assertNotIn("provenance", result)

    def test_apply_modules_preserves_existing_provenance(self):
        from core.modules import apply_modules
        data = {
            "schemaVersion": "winforge.app/v0",
            "modules": [{"type": "chocolatey", "install": {"packages": ["firefox"]}}],
            "provenance": {"builtBy": "test"}
        }
        result = apply_modules(data)
        self.assertEqual(result["provenance"]["builtBy"], "test")
        self.assertIn("moduleExpansions", result["provenance"])

    def test_modulespec_round_trip(self):
        from core.modules import ModuleSpec
        orig = ModuleSpec.from_dict({"type": "chocolatey", "install": {"packages": ["firefox", "7zip.install"]}}, 0)
        d = orig.to_dict()
        restored = ModuleSpec.from_dict(d, 0)
        self.assertEqual(orig.type, restored.type)
        self.assertEqual(orig.install, restored.install)

class ChocolateyModuleManifestTests(unittest.TestCase):
    def test_bluebuild_style_chocolatey_module_expands_to_dependencies_and_install_steps(self):
        manifest = Manifest.from_dict(_module_manifest())

        self.assertEqual([module.type for module in manifest.modules], ["chocolatey"])
        self.assertEqual(manifest.modules[0].install["packages"], ["firefox", "7zip.install"])
        winetricks = [dep for dep in manifest.dependencies if dep.kind == "winetricks"]
        self.assertTrue(winetricks)
        self.assertIn("powershell_core", winetricks[0].verbs)
        self.assertEqual([step.kind for step in manifest.install[:3]], ["script", "choco", "choco"])
        self.assertIn("powershell-wrapper-for-wine", manifest.install[0].command or "")
        self.assertEqual(manifest.install[1].command, "install")
        self.assertEqual(manifest.install[1].args, ["firefox", "-y", "--no-progress"])
        self.assertEqual(manifest.install[2].args, ["7zip.install", "-y", "--no-progress"])
        self.assertEqual(manifest.provenance["moduleExpansions"][0]["type"], "chocolatey")

    def test_strict_yaml_accepts_myos_bluebuild_style_modules_shape(self):
        with tempfile.TemporaryDirectory() as tmp:
            recipe = Path(tmp) / "choco-demo.winforge.yaml"
            recipe.write_text(
                """schemaVersion: winforge.app/v0
name: choco-demo
version: 1.0.0
runtime:
  provider: wine
  version: latest
modules:
  - type: chocolatey
    install:
      packages:
        - firefox
        - 7zip.install
launch:
  entrypoint: C:/Program Files/Mozilla Firefox/firefox.exe
""",
                encoding="utf-8",
            )
            manifest = load_manifest(recipe)

        self.assertEqual(manifest.modules[0].type, "chocolatey")
        self.assertEqual(manifest.modules[0].install["packages"], ["firefox", "7zip.install"])


    def test_public_chocolatey_example_uses_modules_shape(self):
        manifest = load_manifest(Path("examples/chocolatey-firefox.winforge.yaml"))

        self.assertEqual(manifest.modules[0].type, "chocolatey")
        self.assertEqual(manifest.modules[0].install["packages"], ["firefox"])
        self.assertEqual(manifest.install[1].kind, "choco")
        self.assertEqual(manifest.install[1].args, ["firefox", "-y", "--no-progress"])

    def test_chocolatey_module_rejects_shell_like_package_names(self):
        data = _module_manifest()
        data["modules"][0]["install"]["packages"] = ["firefox;touch-/tmp/no"]

        with self.assertRaisesRegex(ManifestError, r"modules\[0\]\.install\.packages\[0\]"):
            Manifest.from_dict(data)


    def test_direct_choco_install_step_requires_install_command_and_args(self):
        data = _module_manifest()
        data.pop("modules")
        data["install"] = [{"kind": "choco"}]

        with self.assertRaisesRegex(ManifestError, r"install\[0\]\.command"):
            Manifest.from_dict(data)

    def test_direct_choco_install_step_rejects_unknown_command(self):
        data = _module_manifest()
        data.pop("modules")
        data["install"] = [{"kind": "choco", "command": "upgrade", "args": ["firefox"]}]

        with self.assertRaisesRegex(ManifestError, r"install\[0\]\.command"):
            Manifest.from_dict(data)


    def test_direct_choco_install_step_rejects_shell_like_args(self):
        data = _module_manifest()
        data.pop("modules")
        data["install"] = [{"kind": "choco", "command": "install", "args": ["firefox;touch-/tmp/no"]}]

        with self.assertRaisesRegex(ManifestError, r"install\[0\]\.args\[0\]"):
            Manifest.from_dict(data)

    def test_unknown_module_type_is_rejected(self):
        data = _module_manifest()
        data["modules"][0]["type"] = "dnf"

        with self.assertRaisesRegex(ManifestError, r"modules\[0\]\.type"):
            Manifest.from_dict(data)


class ChocolateyModuleBuildScriptTests(unittest.TestCase):
    def test_chocolatey_module_generates_setup_before_package_installs(self):
        manifest = Manifest.from_dict(_module_manifest())
        script = generate_build_script(manifest)

        setup_index = script.index("powershell-wrapper-for-wine")
        bootstrap_index = script.index("community.chocolatey.org/install.ps1")
        firefox_index = script.index("Running Chocolatey command: install firefox -y --no-progress")
        zip_index = script.index("Running Chocolatey command: install 7zip.install -y --no-progress")
        self.assertLess(setup_index, bootstrap_index)
        self.assertLess(bootstrap_index, firefox_index)
        self.assertLess(firefox_index, zip_index)
        self.assertIn('wine "$WINEPREFIX/drive_c/Program Files/PowerShell/7/pwsh.exe"', script)
        self.assertIn("$chocoArgs = @(", script)
        self.assertIn("& choco @chocoArgs", script)
        self.assertNotIn("eval choco", script)
        self.assertNotIn('echo "  Running custom script command: set -eu;', script)


if __name__ == "__main__":
    unittest.main()
