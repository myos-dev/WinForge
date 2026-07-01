"""Tests for Phase 6D BYO files and Office-enabling primitives."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from builder.pipeline import generate_build_script
from compat.corpus import load_corpus
from core.manifest import Manifest, ManifestError
from core.sources import verify_manifest_sources


def _sha256(path: Path) -> str:
    import hashlib

    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_byo_files_workspace(root: Path) -> dict[str, str]:
    program_dir = root / "sources/office-files/Program Files/Microsoft Office/root/Office16"
    program_dir.mkdir(parents=True)
    word = program_dir / "WINWORD.EXE"
    excel = program_dir / "EXCEL.EXE"
    word.write_bytes(b"fake word exe\n")
    excel.write_bytes(b"fake excel exe\n")
    installer = root / "sources/office2016/setup.exe"
    installer.parent.mkdir(parents=True)
    installer.write_bytes(b"fake office installer\n")
    return {
        "installer": _sha256(installer),
        "word": _sha256(word),
        "excel": _sha256(excel),
    }


def _office_files_manifest(hashes: dict[str, str]) -> dict[str, object]:
    return {
        "schemaVersion": "winforge.app/v0",
        "name": "office-suite-byo-files",
        "version": "2016-byo",
        "runtime": {"provider": "wine", "version": "latest"},
        "sources": [
            {
                "id": "office-files",
                "type": "files",
                "path": "sources/office-files/Program Files/Microsoft Office",
                "policy": "bring-your-own-files",
                "description": "Customer-provided pre-installed Microsoft Office files tree",
            },
            {
                "id": "office-installer",
                "type": "installer",
                "path": "sources/office2016/setup.exe",
                "sha256": hashes["installer"],
                "policy": "bring-your-own-licensed-media",
            },
        ],
        "profiles": ["office-legacy-32bit"],
        "install": [],
        "filesystem": [
            {
                "source": "sources/office-files/Program Files/Microsoft Office",
                "target": "C:/Program Files/Microsoft Office",
                "mode": "merge",
            }
        ],
        "entrypoints": [
            {
                "id": "word",
                "name": "Microsoft Word",
                "executable": "C:/Program Files/Microsoft Office/root/Office16/WINWORD.EXE",
                "workingDirectory": "C:/Program Files/Microsoft Office/root/Office16",
            },
            {
                "id": "excel",
                "name": "Microsoft Excel",
                "executable": "C:/Program Files/Microsoft Office/root/Office16/EXCEL.EXE",
            },
        ],
        "fileAssociations": [
            {
                "entrypoint": "word",
                "extensions": [".doc", ".docx"],
                "mime": ["application/msword", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"],
            },
            {
                "entrypoint": "excel",
                "extensions": [".xls", ".xlsx", ".csv"],
                "mime": ["application/vnd.ms-excel", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "text/csv"],
            },
        ],
        "launch": {
            "entrypoint": "C:/Program Files/Microsoft Office/root/Office16/WINWORD.EXE",
            "workingDirectory": "C:/Program Files/Microsoft Office/root/Office16",
        },
        "provenance": {"sources": []},
    }


class ByoSourcePolicyAndFilesModuleTests(unittest.TestCase):
    def test_sources_normalize_byo_policy_and_files_type(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            hashes = _write_byo_files_workspace(root)
            manifest = Manifest.from_dict(_office_files_manifest(hashes))
            result = verify_manifest_sources(manifest, workspace=root)

        self.assertTrue(result["valid"], result["errors"])
        declared = {item["sourceId"]: item for item in result["items"] if item.get("usage") == "declared-source"}
        self.assertEqual(declared["office-files"]["sourceType"], "files")
        self.assertEqual(declared["office-files"]["sourcePolicy"], "bring-your-own-files")
        self.assertEqual(declared["office-files"]["status"], "present")
        self.assertEqual(declared["office-installer"]["sourcePolicy"], "bring-your-own-licensed-media")
        self.assertEqual(declared["office-installer"]["status"], "verified")

    def test_filesystem_merge_layers_folder_contents_into_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            hashes = _write_byo_files_workspace(root)
            manifest = Manifest.from_dict(_office_files_manifest(hashes))

        self.assertEqual(manifest.filesystem[0].mode, "merge")
        script = generate_build_script(manifest, workspace_mount="/workspace")
        self.assertIn('Merge /workspace/sources/office-files/Program Files/Microsoft Office -> $WINEPREFIX/drive_c/Program Files/Microsoft Office', script)
        self.assertIn('cp -a "/workspace/sources/office-files/Program Files/Microsoft Office/." "$WINEPREFIX/drive_c/Program Files/Microsoft Office/"', script)
        self.assertNotIn('cp -r "/workspace/sources/office-files/Program Files/Microsoft Office" "$WINEPREFIX/drive_c/Program Files/Microsoft Office"', script)

    def test_invalid_source_policy_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            hashes = _write_byo_files_workspace(root)
            data = _office_files_manifest(hashes)
            data["sources"][0]["policy"] = "download-cracked-archive"
            with self.assertRaisesRegex(ManifestError, "sources\\[0\\].policy"):
                Manifest.from_dict(data)


class SuiteEntrypointTests(unittest.TestCase):
    def test_manifest_records_suite_entrypoints_and_file_associations(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            hashes = _write_byo_files_workspace(root)
            manifest = Manifest.from_dict(_office_files_manifest(hashes))

        self.assertEqual([entry.id for entry in manifest.entrypoints], ["word", "excel"])
        serialized = manifest.to_dict()
        self.assertEqual(serialized["entrypoints"][0]["name"], "Microsoft Word")
        self.assertEqual(serialized["fileAssociations"][0]["entrypoint"], "word")
        self.assertIn(".docx", serialized["fileAssociations"][0]["extensions"])

    def test_file_association_must_reference_known_entrypoint(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            hashes = _write_byo_files_workspace(root)
            data = _office_files_manifest(hashes)
            data["fileAssociations"][0]["entrypoint"] = "publisher"
            with self.assertRaisesRegex(ManifestError, "fileAssociations\\[0\\].entrypoint"):
                Manifest.from_dict(data)


class OfficeProfileTests(unittest.TestCase):
    def test_office_legacy_profile_expands_compatibility_and_winetricks_dependencies(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            hashes = _write_byo_files_workspace(root)
            manifest = Manifest.from_dict(_office_files_manifest(hashes))

        self.assertEqual(manifest.profiles, ["office-legacy-32bit"])
        self.assertEqual(manifest.compatibility["arch"], "win32")
        self.assertEqual(manifest.compatibility["windowsVersion"], "win7")
        self.assertEqual(manifest.compatibility["dllPolicy"]["gdiplus"], "native,builtin")
        self.assertEqual(manifest.compatibility["dllPolicy"]["riched20"], "native,builtin")
        winetricks = [dep for dep in manifest.dependencies if dep.kind == "winetricks"]
        self.assertTrue(winetricks)
        verbs = set(winetricks[0].verbs)
        self.assertIn("dotnet40", verbs)
        self.assertIn("msxml4", verbs)
        self.assertIn("mspatcha", verbs)
        self.assertIn("allfonts", verbs)

    def test_explicit_compatibility_overrides_profile_defaults(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            hashes = _write_byo_files_workspace(root)
            data = _office_files_manifest(hashes)
            data["compatibility"] = {
                "arch": "win64",
                "windowsVersion": "win10",
                "dllPolicy": {"riched20": "builtin,native"},
            }
            manifest = Manifest.from_dict(data)

        self.assertEqual(manifest.compatibility["arch"], "win64")
        self.assertEqual(manifest.compatibility["windowsVersion"], "win10")
        self.assertEqual(manifest.compatibility["dllPolicy"]["gdiplus"], "native,builtin")
        self.assertEqual(manifest.compatibility["dllPolicy"]["riched20"], "builtin,native")

    def test_corpus_contains_office_byo_files_and_installer_entries(self):
        corpus = load_corpus()
        slugs = {app["slug"]: app for app in corpus["apps"]}
        self.assertIn("microsoft-office-legacy-byo-installer", slugs)
        self.assertIn("microsoft-office-byo-files", slugs)
        self.assertEqual(slugs["microsoft-office-byo-files"]["sourcePolicy"], "bring-your-own-files")


if __name__ == "__main__":
    unittest.main()
