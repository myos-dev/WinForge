"""Tests for Phase 6A compatibility policy support."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from artifact.bundle import create_bundle
from artifact.graph import build_execution_graph
from artifact.oci import create_oci_export_plan, prepare_oci_build_context
from builder.pipeline import generate_build_script
from core.compatibility import compile_wine_dll_overrides
from runtime.launcher import build_run_plan
from core.manifest import Manifest, ManifestError


BASE_MANIFEST = {
    "schemaVersion": "winforge.app/v0",
    "name": "hard-app",
    "version": "2.0.0",
    "runtime": {"provider": "staging", "version": "latest"},
    "dependencies": [{"kind": "winetricks", "verbs": ["corefonts"]}],
    "install": [{"kind": "exe", "source": "file://sources/setup.exe", "args": ["/S"]}],
    "filesystem": [],
    "launch": {
        "entrypoint": "C:/Program Files/HardApp/hard.exe",
        "workingDirectory": "C:/Program Files/HardApp",
    },
    "state": {"persistence": "persistent"},
    "exports": [{"name": "reports", "path": "C:/users/winforge/Documents"}],
    "provenance": {"sources": []},
}


FIRST_CLASS_COMPATIBILITY = {
    "arch": "win64",
    "windowsVersion": "win10",
    "graphics": {
        "backend": "dxvk",
        "fallback": "wined3d",
    },
    "dllPolicy": {
        "d3d11": "native,builtin",
        "d3dcompiler_47": "native",
        "mscoree": "disabled",
        "mshtml": "disabled",
    },
    "env": {
        "DXVK_LOG_LEVEL": "none",
        "WINEDEBUG": "-all",
    },
}


class CompatibilityPolicyManifestTests(unittest.TestCase):
    def test_manifest_accepts_and_normalizes_first_class_compatibility_policy(self):
        data = json.loads(json.dumps(BASE_MANIFEST))
        data["compatibility"] = FIRST_CLASS_COMPATIBILITY

        manifest = Manifest.from_dict(data)

        self.assertEqual(manifest.compatibility["schemaVersion"], "winforge.compatibility-policy/v0")
        self.assertEqual(manifest.compatibility["arch"], "win64")
        self.assertEqual(manifest.compatibility["windowsVersion"], "win10")
        self.assertEqual(manifest.compatibility["graphics"], {"backend": "dxvk", "fallback": "wined3d"})
        self.assertEqual(manifest.compatibility["dllPolicy"]["d3d11"], "native,builtin")
        self.assertEqual(manifest.compatibility["dllPolicy"]["mshtml"], "disabled")
        self.assertEqual(manifest.compatibility["env"]["DXVK_LOG_LEVEL"], "none")
        self.assertEqual(manifest.to_dict()["compatibility"], manifest.compatibility)

    def test_legacy_config_wine_values_normalize_into_compatibility_policy(self):
        data = json.loads(json.dumps(BASE_MANIFEST))
        data["config"] = {
            "wine": {
                "arch": "win32",
                "windowsVersion": "win7",
                "dllOverrides": {
                    "mscoree": "disabled",
                    "mshtml": "disabled",
                    "riched20": "native,builtin",
                },
            },
            "graphics": {
                "backend": "wined3d",
            },
            "env": {
                "WINEDEBUG": "-all",
            },
        }

        manifest = Manifest.from_dict(data)

        self.assertEqual(manifest.compatibility["arch"], "win32")
        self.assertEqual(manifest.compatibility["windowsVersion"], "win7")
        self.assertEqual(manifest.compatibility["graphics"], {"backend": "wined3d"})
        self.assertEqual(manifest.compatibility["dllPolicy"], {
            "mscoree": "disabled",
            "mshtml": "disabled",
            "riched20": "native,builtin",
        })
        self.assertEqual(manifest.compatibility["env"], {"WINEDEBUG": "-all"})

    def test_first_class_compatibility_overrides_legacy_config_values(self):
        data = json.loads(json.dumps(BASE_MANIFEST))
        data["config"] = {
            "wine": {
                "arch": "win32",
                "windowsVersion": "win7",
                "dllOverrides": {"mscoree": "disabled"},
            },
            "graphics": {"backend": "wined3d"},
        }
        data["compatibility"] = {
            "arch": "win64",
            "windowsVersion": "win10",
            "graphics": {"backend": "dxvk"},
            "dllPolicy": {"mscoree": "native,builtin"},
        }

        manifest = Manifest.from_dict(data)

        self.assertEqual(manifest.compatibility["arch"], "win64")
        self.assertEqual(manifest.compatibility["windowsVersion"], "win10")
        self.assertEqual(manifest.compatibility["graphics"], {"backend": "dxvk"})
        self.assertEqual(manifest.compatibility["dllPolicy"], {"mscoree": "native,builtin"})

    def test_manifest_rejects_invalid_compatibility_policy(self):
        data = json.loads(json.dumps(BASE_MANIFEST))
        data["compatibility"] = {"graphics": {"backend": "metal"}}

        with self.assertRaisesRegex(ManifestError, "compatibility.graphics.backend"):
            Manifest.from_dict(data)

    def test_manifest_rejects_invalid_dll_policy_value(self):
        data = json.loads(json.dumps(BASE_MANIFEST))
        data["compatibility"] = {"dllPolicy": {"d3d11": "maybe-native"}}

        with self.assertRaisesRegex(ManifestError, "compatibility.dllPolicy.d3d11"):
            Manifest.from_dict(data)


class CompatibilityPolicyApplicationTests(unittest.TestCase):
    def test_dll_policy_compiles_to_deterministic_wine_overrides(self):
        overrides = compile_wine_dll_overrides({
            "mshtml": "disabled",
            "d3d11": "native,builtin",
            "mscoree": "disabled",
            "d3dcompiler_47": "native",
            "dxgi": "builtin,native",
        })

        self.assertEqual(
            overrides,
            "d3d11=n,b;d3dcompiler_47=n;dxgi=b,n;mscoree=;mshtml=",
        )

    def test_graph_records_requested_compatibility_policy(self):
        data = json.loads(json.dumps(BASE_MANIFEST))
        data["compatibility"] = FIRST_CLASS_COMPATIBILITY
        manifest = Manifest.from_dict(data)

        graph = build_execution_graph(manifest)

        self.assertTrue(graph["compatibility"]["requiresExactRuntime"])
        self.assertEqual(graph["compatibility"]["requestedPolicy"], manifest.compatibility)
        self.assertEqual(graph["compatibility"]["requestedPolicy"]["graphics"]["backend"], "dxvk")

    def test_bundle_records_compatibility_policy_in_manifest_graph_and_provenance(self):
        data = json.loads(json.dumps(BASE_MANIFEST))
        data["compatibility"] = FIRST_CLASS_COMPATIBILITY
        manifest = Manifest.from_dict(data)

        with tempfile.TemporaryDirectory() as tmp:
            bundle = create_bundle(manifest, Path(tmp), dry_run=True)
            manifest_payload = json.loads((bundle / "manifest.winforge.json").read_text(encoding="utf-8"))
            graph = json.loads((bundle / "metadata/graph.json").read_text(encoding="utf-8"))
            provenance = json.loads((bundle / "metadata/provenance.json").read_text(encoding="utf-8"))

        self.assertEqual(manifest_payload["compatibility"]["graphics"]["backend"], "dxvk")
        self.assertEqual(graph["compatibility"]["requestedPolicy"]["dllPolicy"]["d3d11"], "native,builtin")
        self.assertEqual(provenance["compatibility"]["windowsVersion"], "win10")

    def test_build_script_applies_compatibility_policy_before_installing_apps(self):
        data = json.loads(json.dumps(BASE_MANIFEST))
        data["compatibility"] = FIRST_CLASS_COMPATIBILITY
        manifest = Manifest.from_dict(data)

        script = generate_build_script(manifest)

        self.assertIn("[winforge] Compatibility policy", script)
        self.assertIn("export WINEARCH='win64'", script)
        self.assertIn("export WINFORGE_GRAPHICS_BACKEND='dxvk'", script)
        self.assertIn("export WINFORGE_GRAPHICS_FALLBACK='wined3d'", script)
        self.assertIn("export DXVK_LOG_LEVEL='none'", script)
        self.assertIn("export WINEDEBUG='-all'", script)
        self.assertIn("export WINEDLLOVERRIDES='d3d11=n,b;d3dcompiler_47=n;mscoree=;mshtml='", script)
        self.assertIn("winecfg -v win10", script)
        self.assertIn("winetricks -q dxvk", script)
        self.assertLess(script.index("winecfg -v win10"), script.index("### Phase 2: install-dependencies"))
        self.assertLess(script.index("winetricks -q dxvk"), script.index("### Phase 2: install-dependencies"))

    def test_run_plan_exports_compatibility_policy_for_application_launch(self):
        data = json.loads(json.dumps(BASE_MANIFEST))
        data["compatibility"] = FIRST_CLASS_COMPATIBILITY
        manifest = Manifest.from_dict(data)

        with tempfile.TemporaryDirectory() as tmp:
            bundle = create_bundle(manifest, Path(tmp), dry_run=True)
            plan = build_run_plan(bundle, graphics="headless", engine="podman")

        env = plan["container"]["environment"]
        script = plan["container"]["script"]
        self.assertEqual(env["WINEARCH"], "win64")
        self.assertEqual(env["WINFORGE_GRAPHICS_BACKEND"], "dxvk")
        self.assertEqual(env["WINFORGE_GRAPHICS_FALLBACK"], "wined3d")
        self.assertEqual(env["DXVK_LOG_LEVEL"], "none")
        self.assertEqual(env["WINEDEBUG"], "-all")
        self.assertEqual(env["WINEDLLOVERRIDES"], "d3d11=n,b;d3dcompiler_47=n;mscoree=;mshtml=")
        self.assertIn("export WINEDLLOVERRIDES=", script)
        self.assertIn("export WINFORGE_GRAPHICS_BACKEND=dxvk", script)

    def test_oci_app_launcher_exports_compatibility_policy_from_embedded_graph(self):
        data = json.loads(json.dumps(BASE_MANIFEST))
        data["compatibility"] = FIRST_CLASS_COMPATIBILITY
        manifest = Manifest.from_dict(data)

        with tempfile.TemporaryDirectory() as tmp:
            bundle = create_bundle(manifest, Path(tmp) / "dist", dry_run=True)
            plan = create_oci_export_plan(bundle, tag="local/hard-app:2.0.0")
            context = prepare_oci_build_context(bundle, plan, Path(tmp) / "context")
            launcher = (context / "winforge-app-launch").read_text(encoding="utf-8")

        self.assertIn("requestedPolicy", launcher)
        self.assertIn("WINEDLLOVERRIDES", launcher)
        self.assertIn("WINFORGE_GRAPHICS_BACKEND", launcher)
        self.assertIn("native,builtin", launcher)
        self.assertIn("n,b", launcher)


if __name__ == "__main__":
    unittest.main()
