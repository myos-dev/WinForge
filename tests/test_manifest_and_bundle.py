from __future__ import annotations
import json, tempfile, unittest
from pathlib import Path
from artifact.bundle import create_bundle
from builder.pipeline import build_plan
from core.manifest import Manifest, ManifestError
VALID = {"schemaVersion":"winforge.dev/v0","name":"sample","version":"1.0.0","runtime":{"provider":"wine","version":"9.0"},"dependencies":[{"kind":"winetricks","verbs":["corefonts"]}],"install":[{"kind":"portable","source":"file://app.zip","target":"C:/Program Files/App"}],"filesystem":[{"source":"config.ini","target":"C:/Program Files/App/config.ini"}],"launch":{"entrypoint":"C:/Program Files/App/App.exe"},"provenance":{"sources":[]}}
class ManifestAndBundleTests(unittest.TestCase):
    def test_valid_manifest_parses(self):
        manifest = Manifest.from_dict(VALID); self.assertEqual(manifest.name, "sample"); self.assertEqual(manifest.runtime.provider, "wine")
    def test_rejects_unknown_runtime_provider(self):
        invalid = json.loads(json.dumps(VALID)); invalid["runtime"]["provider"] = "steam-only"
        with self.assertRaises(ManifestError): Manifest.from_dict(invalid)
    def test_plan_contains_required_phase_order(self):
        phases = [x["phase"] for x in build_plan(Manifest.from_dict(VALID))]
        self.assertEqual(phases, ["init-prefix","install-dependencies","install-apps","apply-layout-and-registry","validate","seal-artifact"])
    def test_dry_run_bundle_writes_contract_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = create_bundle(Manifest.from_dict(VALID), Path(tmp), dry_run=True)
            for rel in ["manifest.winforge.json","runtime/runtime.json","launch/entrypoint.json","metadata/provenance.json","build/build-plan.json"]:
                self.assertTrue((path/rel).exists(), rel)
if __name__ == "__main__": unittest.main()
