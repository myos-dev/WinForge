from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
import zipfile
from subprocess import CalledProcessError, CompletedProcess, PIPE
from unittest import mock
from pathlib import Path

from core.manifest import Manifest
from core.media import MediaStageError, stage_media
from core.sources import audit_manifest_sources


class MediaStagingTests(unittest.TestCase):
    def test_stage_media_directory_normalizes_modes_and_writes_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "incoming-media"
            nested = source / "Office.en-us"
            nested.mkdir(parents=True)
            setup = source / "setup.exe"
            setup.write_bytes(b"fake setup")
            cab = nested / "office.cab"
            cab.write_bytes(b"fake cab")
            os.chmod(setup, 0o400)
            os.chmod(cab, 0o400)

            result = stage_media(source, name="office2010-byo", workspace=root)

            staged = root / "sources" / "office2010-byo" / "media"
            metadata_path = root / "sources" / "office2010-byo" / "metadata" / "media-stage.json"
            self.assertEqual(result["schemaVersion"], "winforge.media-stage/v0")
            self.assertTrue(result["success"])
            self.assertEqual(Path(result["stagedPath"]), staged)
            self.assertEqual(result["summary"]["fileCount"], 2)
            self.assertEqual(result["summary"]["byteSize"], len(b"fake setup") + len(b"fake cab"))
            self.assertTrue((staged / "setup.exe").exists())
            self.assertTrue((staged / "Office.en-us" / "office.cab").exists())
            self.assertTrue((staged / "setup.exe").stat().st_mode & stat.S_IWUSR)
            self.assertTrue((staged / "Office.en-us" / "office.cab").stat().st_mode & stat.S_IWUSR)
            self.assertTrue(metadata_path.exists())
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            self.assertEqual(metadata["stagedPath"], str(staged))

    def test_stage_media_rejects_archive_path_traversal(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive = root / "bad.zip"
            with zipfile.ZipFile(archive, "w") as zf:
                zf.writestr("../escape.txt", "nope")

            with self.assertRaisesRegex(MediaStageError, "unsafe path"):
                stage_media(archive, name="bad-media", workspace=root)

    def test_stage_media_wraps_unsupported_zip_member_as_media_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive = root / "unsupported.zip"
            with zipfile.ZipFile(archive, "w") as zf:
                zf.writestr("setup.exe", "fake setup")

            with mock.patch("core.media.zipfile.ZipFile.open", side_effect=NotImplementedError("unsupported compression")):
                with self.assertRaisesRegex(MediaStageError, "failed to stage media"):
                    stage_media(archive, name="unsupported", workspace=root)

    def test_stage_media_rejects_symlinked_staging_ancestor(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            outside = root / "outside"
            outside.mkdir()
            source = root / "incoming"
            source.mkdir()
            (source / "setup.exe").write_bytes(b"fake setup")
            os.symlink(outside, root / "sources")

            with self.assertRaisesRegex(MediaStageError, "symlink"):
                stage_media(source, name="unsafe", workspace=root)

            self.assertFalse((outside / "unsafe" / "media" / "setup.exe").exists())

    def test_stage_media_wraps_iso_extractor_failure_as_media_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            iso = root / "media.iso"
            iso.write_bytes(b"not a real iso")

            def fake_which(name):
                return "/usr/bin/bsdtar" if name == "bsdtar" else None

            with mock.patch("core.media.shutil.which", side_effect=fake_which), \
                 mock.patch("core.media.subprocess.run", side_effect=CalledProcessError(2, ["bsdtar"])):
                with self.assertRaisesRegex(MediaStageError, "ISO extraction failed"):
                    stage_media(iso, name="bad-iso", workspace=root)

    def test_stage_media_rejects_metadata_file_symlink(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "incoming"
            source.mkdir()
            (source / "setup.exe").write_bytes(b"fake setup")
            metadata = root / "sources" / "safe" / "metadata"
            metadata.mkdir(parents=True)
            outside = root / "outside.txt"
            outside.write_text("do-not-overwrite", encoding="utf-8")
            os.symlink(outside, metadata / "media-stage.json")

            with self.assertRaisesRegex(MediaStageError, "symlink"):
                stage_media(source, name="safe", workspace=root)

            self.assertEqual(outside.read_text(encoding="utf-8"), "do-not-overwrite")

    def test_stage_media_rejects_symlink_inside_existing_media_before_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "incoming"
            source.mkdir()
            (source / "setup.exe").write_bytes(b"fake setup")
            media = root / "sources" / "safe" / "media"
            media.mkdir(parents=True)
            outside = root / "outside.txt"
            outside.write_text("keep", encoding="utf-8")
            os.symlink(outside, media / "external-link")

            with self.assertRaisesRegex(MediaStageError, "symlink"):
                stage_media(source, name="safe", workspace=root, overwrite=True)

            self.assertEqual(outside.read_text(encoding="utf-8"), "keep")

    def test_stage_media_wraps_setup_io_errors_as_media_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "incoming"
            source.mkdir()
            (source / "setup.exe").write_bytes(b"fake setup")
            (root / "sources").write_text("not a directory", encoding="utf-8")

            with self.assertRaisesRegex(MediaStageError, "failed to stage media"):
                stage_media(source, name="safe", workspace=root)

    def test_stage_media_rejects_source_that_would_contain_staging_destination(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "setup.exe").write_bytes(b"fake setup")

            with self.assertRaisesRegex(MediaStageError, "would contain staged output"):
                stage_media(root, name="recursive", workspace=root)

    def test_stage_media_captures_iso_extractor_output_to_keep_cli_json_clean(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            iso = root / "media.iso"
            iso.write_bytes(b"fake iso")

            def fake_which(name):
                return "/usr/bin/bsdtar" if name == "bsdtar" else None

            with mock.patch("core.media.shutil.which", side_effect=fake_which), \
                 mock.patch("core.media.subprocess.run", return_value=CompletedProcess(["bsdtar"], 0)) as run:
                result = stage_media(iso, name="quiet-iso", workspace=root)

            self.assertEqual(result["sourceKind"], "iso")
            self.assertEqual(run.call_args.kwargs["stdout"], PIPE)
            self.assertEqual(run.call_args.kwargs["stderr"], PIPE)


class SourcePolicyAuditTests(unittest.TestCase):
    def test_audit_manifest_sources_blocks_activation_kms_artifacts_without_reading_contents(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            media = root / "sources" / "office2010" / "media"
            suspicious = media / "Online_KMS_Activation" / "Activate.cmd"
            suspicious.parent.mkdir(parents=True)
            (media / "setup.exe").write_bytes(b"fake setup")
            suspicious.write_text("SECRET_SHOULD_NOT_APPEAR_IN_AUDIT\n", encoding="utf-8")
            manifest = Manifest.from_dict({
                "schemaVersion": "winforge.app/v0",
                "name": "office2010-policy-audit",
                "version": "test",
                "runtime": {"provider": "wine", "version": "9.0"},
                "sources": [{
                    "id": "office-media",
                    "type": "files",
                    "path": "sources/office2010/media",
                    "policy": "bring-your-own-licensed-media",
                }],
                "install": [{"kind": "exe", "source": "sources/office2010/media/setup.exe"}],
                "launch": {"entrypoint": "C:/Program Files/Microsoft Office/Office14/WINWORD.EXE"},
                "provenance": {"sources": []},
            })

            result = audit_manifest_sources(manifest, workspace=root)

            serialized = json.dumps(result, sort_keys=True)
            self.assertEqual(result["schemaVersion"], "winforge.source-policy/v0")
            self.assertFalse(result["valid"])
            self.assertEqual(result["summary"]["blocked"], 1)
            self.assertIn("Online_KMS_Activation/Activate.cmd", serialized)
            self.assertIn("activation", result["findings"][0]["ruleId"])
            self.assertNotIn("SECRET_SHOULD_NOT_APPEAR_IN_AUDIT", serialized)

    def test_audit_manifest_sources_blocks_suspicious_root_source_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            media = root / "sources" / "KMSAuto"
            media.mkdir(parents=True)
            (media / "setup.exe").write_bytes(b"fake setup")
            manifest = Manifest.from_dict({
                "schemaVersion": "winforge.app/v0",
                "name": "root-policy-audit",
                "version": "test",
                "runtime": {"provider": "wine", "version": "9.0"},
                "sources": [{
                    "id": "media",
                    "type": "files",
                    "path": "sources/KMSAuto",
                    "policy": "bring-your-own-licensed-media",
                }],
                "launch": {"entrypoint": "C:/Program Files/App/App.exe"},
                "provenance": {"sources": []},
            })

            result = audit_manifest_sources(manifest, workspace=root)

            self.assertFalse(result["valid"])
            self.assertEqual(result["summary"]["blocked"], 1)
            self.assertEqual(result["findings"][0]["path"], "KMSAuto")

    def test_audit_manifest_sources_accepts_clean_media_and_reports_missing_sources(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            media = root / "sources" / "clean-media"
            media.mkdir(parents=True)
            (media / "setup.exe").write_bytes(b"fake setup")
            data = {
                "schemaVersion": "winforge.app/v0",
                "name": "clean-policy-audit",
                "version": "test",
                "runtime": {"provider": "wine", "version": "9.0"},
                "sources": [{
                    "id": "clean-media",
                    "type": "files",
                    "path": "sources/clean-media",
                    "policy": "bring-your-own-licensed-media",
                }],
                "install": [{"kind": "exe", "source": "sources/clean-media/setup.exe"}],
                "launch": {"entrypoint": "C:/Program Files/App/App.exe"},
                "provenance": {"sources": []},
            }
            clean = audit_manifest_sources(Manifest.from_dict(data), workspace=root)
            self.assertTrue(clean["valid"], clean)
            self.assertEqual(clean["summary"]["blocked"], 0)

            data["sources"][0]["path"] = "sources/missing-media"
            missing = audit_manifest_sources(Manifest.from_dict(data), workspace=root)
            self.assertFalse(missing["valid"])
            self.assertEqual(missing["summary"]["errors"], 1)
            self.assertIn("missing local source", missing["errors"][0])

    def test_sources_audit_cli_reports_policy_findings(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            media = root / "sources" / "office2010" / "media"
            (media / "Online_KMS_Activation").mkdir(parents=True)
            (media / "setup.exe").write_bytes(b"fake setup")
            (media / "Online_KMS_Activation" / "Activate.cmd").write_text("do not read", encoding="utf-8")
            manifest = root / "recipe.json"
            manifest.write_text(json.dumps({
                "schemaVersion": "winforge.app/v0",
                "name": "office2010-policy-audit-cli",
                "version": "test",
                "runtime": {"provider": "wine", "version": "9.0"},
                "sources": [{
                    "id": "office-media",
                    "type": "files",
                    "path": "sources/office2010/media",
                    "policy": "bring-your-own-licensed-media",
                }],
                "install": [{"kind": "exe", "source": "sources/office2010/media/setup.exe"}],
                "launch": {"entrypoint": "C:/Program Files/Microsoft Office/Office14/WINWORD.EXE"},
                "provenance": {"sources": []},
            }), encoding="utf-8")

            proc = subprocess.run(
                [sys.executable, "cmd/winforge.py", "sources", "audit", str(manifest), "--workspace", str(root)],
                cwd=Path(__file__).resolve().parents[1],
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(proc.returncode, 8, proc.stdout + proc.stderr)
            payload = json.loads(proc.stdout)
            self.assertFalse(payload["valid"])
            self.assertEqual(payload["summary"]["blocked"], 1)

    def test_media_stage_cli_writes_staged_media(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "incoming"
            source.mkdir()
            (source / "setup.exe").write_bytes(b"fake setup")

            proc = subprocess.run(
                [
                    sys.executable,
                    "cmd/winforge.py",
                    "media",
                    "stage",
                    str(source),
                    "--name",
                    "office2010-byo",
                    "--workspace",
                    str(root),
                ],
                cwd=Path(__file__).resolve().parents[1],
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            payload = json.loads(proc.stdout)
            staged = root / "sources" / "office2010-byo" / "media"
            self.assertEqual(Path(payload["stagedPath"]), staged)
            self.assertTrue((staged / "setup.exe").exists())


if __name__ == "__main__":
    unittest.main()
