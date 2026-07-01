"""Tests for Phase 6F Wine runner cache and diagnostics."""
from __future__ import annotations

import hashlib
import json
import os
import stat
import struct
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

from artifact.bundle import create_bundle
from core.manifest import Manifest
from runtime.launcher import build_run_plan
from runtime.runner_cache import diagnose_runner, ensure_runner
from runtime.runner_catalog import RunnerSpec, resolve_runner_spec


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def _make_runner_tarball(path: Path, version: str = '8.2') -> None:
    root = path.parent / f'PlayOnLinux-wine-{version}-upstream-linux-x86'
    wine = root / 'bin' / 'wine'
    wine.parent.mkdir(parents=True)
    wine.write_text(f'#!/bin/sh\necho wine-{version}\n', encoding='utf-8')
    wine.chmod(wine.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    (wine.parent / 'wine-alias').symlink_to('wine')
    with tarfile.open(path, 'w:gz') as tar:
        tar.add(root, arcname=root.name)


def _write_fake_elf_with_missing_interpreter(path: Path, interpreter: str = '/missing/ld-linux.so.2') -> None:
    # Minimal 32-bit little-endian ELF with one PT_INTERP program header. It is
    # not a runnable Wine binary, but it exercises the same missing-interpreter
    # diagnostic path produced by old 32-bit upstream Wine builds on hosts that
    # lack /lib/ld-linux.so.2.
    ident = b'\x7fELF' + bytes([1, 1, 1, 0]) + b'\x00' * 8
    ehdr = struct.pack(
        '<16sHHIIIIIHHHHHH',
        ident,
        2,      # ET_EXEC
        3,      # EM_386
        1,
        0,
        52,     # e_phoff
        0,
        0,
        52,
        32,
        1,
        0,
        0,
        0,
    )
    interp = interpreter.encode('utf-8') + b'\x00'
    interp_offset = 52 + 32
    phdr = struct.pack(
        '<IIIIIIII',
        3,  # PT_INTERP
        interp_offset,
        0,
        0,
        len(interp),
        len(interp),
        4,
        1,
    )
    path.write_bytes(ehdr + phdr + interp)
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def runner_manifest_data() -> dict:
    return {
        'schemaVersion': 'winforge.app/v0',
        'name': 'runner-aware-suite',
        'version': '1.0.0',
        'runtime': {'provider': 'wine', 'version': '9.0', 'runner': 'pol-8.2'},
        'launch': {'entrypoint': 'C:/Program Files/App/App.exe'},
        'provenance': {'sources': []},
    }


class RunnerCatalogTests(unittest.TestCase):
    def test_builtin_pol_runner_alias_resolves_to_phoenicis_upstream_tarball(self):
        spec = resolve_runner_spec('pol-8.2')

        self.assertEqual(spec.id, 'pol-8.2')
        self.assertEqual(spec.provider, 'wine')
        self.assertEqual(spec.version, '8.2')
        self.assertEqual(spec.arch, 'x86')
        self.assertEqual(spec.source, 'playonlinux-phoenicis-upstream')
        self.assertIn('phoenicis/upstream-linux-x86', spec.url)
        self.assertIn('PlayOnLinux-wine-8.2-upstream-linux-x86.tar.gz', spec.url)
        self.assertEqual(len(spec.sha256 or ''), 64)
        self.assertEqual(spec.sha256, 'd38ed5362564c0de73a6f4720a20cf6eece569d2455be2567ac41e1a8a5cb0d6')

    def test_runner_cache_extracts_local_tarball_and_records_provenance(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            tarball = tmp / 'runner.tar.gz'
            _make_runner_tarball(tarball, version='8.2')
            spec = RunnerSpec(
                id='fixture-pol-8.2',
                provider='wine',
                version='8.2',
                arch='x86',
                source='fixture',
                url=tarball.as_uri(),
                sha256=_sha256(tarball),
                strip_components=1,
            )

            result = ensure_runner(spec, cache_dir=tmp / 'cache')

            wine = Path(result['winePath'])
            self.assertTrue(wine.exists(), result)
            self.assertEqual(result['schemaVersion'], 'winforge.runner-cache/v0')
            self.assertEqual(result['runner']['id'], 'fixture-pol-8.2')
            self.assertEqual(result['runner']['version'], '8.2')
            self.assertEqual(result['runner']['sha256'], _sha256(tarball))
            self.assertEqual(result['archive']['sha256'], _sha256(tarball))
            self.assertIn(result['status'], {'installed', 'present'})
            self.assertTrue((wine.parent / 'wine-alias').is_symlink())

    def test_runner_cache_handles_dot_root_tarballs_like_playonlinux(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            dotroot = tmp / 'dotroot'
            wine = dotroot / 'bin' / 'wine'
            wine.parent.mkdir(parents=True)
            wine.write_text('#!/bin/sh\necho wine-dotroot\n', encoding='utf-8')
            wine.chmod(wine.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            tarball = tmp / 'dotroot.tar.gz'
            with tarfile.open(tarball, 'w:gz') as tar:
                tar.add(wine.parent, arcname='./bin')
            spec = RunnerSpec(
                id='dotroot-runner',
                provider='wine',
                version='dotroot',
                arch='x86',
                source='fixture',
                url=tarball.as_uri(),
                sha256=_sha256(tarball),
                strip_components=1,
            )

            result = ensure_runner(spec, cache_dir=tmp / 'cache')

            self.assertEqual(result['diagnostic']['status'], 'ok', result)
            self.assertTrue(Path(result['winePath']).exists(), result)
            self.assertEqual(Path(result['winePath']).relative_to(tmp / 'cache' / 'dotroot-runner'), Path('bin/wine'))

    def test_runner_diagnostic_reports_missing_elf_interpreter(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            runner = Path(tmpdir) / 'pol-8.2'
            wine = runner / 'bin' / 'wine'
            wine.parent.mkdir(parents=True)
            _write_fake_elf_with_missing_interpreter(wine)

            result = diagnose_runner(runner)

            self.assertEqual(result['schemaVersion'], 'winforge.runner-diagnostic/v0')
            self.assertFalse(result['executable'])
            self.assertEqual(result['status'], 'missing-elf-interpreter')
            self.assertEqual(result['elf']['interpreter'], '/missing/ld-linux.so.2')
            self.assertFalse(result['elf']['interpreterExists'])
            self.assertIn('32-bit compatibility libraries', result['recommendation'])

    def test_cli_runners_list_reports_builtin_aliases(self):
        proc = subprocess.run(
            [sys.executable, 'cmd/winforge.py', 'runners', 'list'],
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(proc.returncode, 0, proc.stderr)
        payload = json.loads(proc.stdout)
        ids = [item['id'] for item in payload['runners']]
        self.assertIn('pol-8.2', ids)
        self.assertIn('pol-4.3', ids)
        self.assertIn('pol-3.0.3', ids)

    def test_cli_runner_ensure_accepts_custom_url_without_catalog_alias(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            tarball = tmp / 'runner.tar.gz'
            _make_runner_tarball(tarball, version='custom')
            proc = subprocess.run(
                [
                    sys.executable,
                    'cmd/winforge.py',
                    'runners',
                    'ensure',
                    'custom-runner',
                    '--url',
                    tarball.as_uri(),
                    '--sha256',
                    _sha256(tarball),
                    '--cache-dir',
                    str(tmp / 'cache'),
                ],
                capture_output=True,
                text=True,
                check=False,
            )

        self.assertEqual(proc.returncode, 0, proc.stderr)
        payload = json.loads(proc.stdout)
        self.assertEqual(payload['runner']['id'], 'custom-runner')
        self.assertEqual(payload['runner']['provider'], 'wine')
        self.assertEqual(payload['runner']['version'], 'custom-runner')
        self.assertEqual(payload['diagnostic']['status'], 'ok')


class RunnerManifestIntegrationTests(unittest.TestCase):
    def test_runtime_runner_is_normalized_into_graph_and_run_plan(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            bundle = create_bundle(Manifest.from_dict(runner_manifest_data()), Path(tmpdir), dry_run=True)
            graph = json.loads((bundle / 'metadata' / 'graph.json').read_text(encoding='utf-8'))
            plan = build_run_plan(bundle, graphics='headless', engine='podman')

        self.assertEqual(graph['runnerRuntime']['runner'], 'pol-8.2')
        self.assertEqual(graph['runnerRuntime']['runnerVersion'], '8.2')
        self.assertEqual(graph['runnerRuntime']['runnerSource'], 'playonlinux-phoenicis-upstream')
        self.assertIn('PlayOnLinux-wine-8.2-upstream-linux-x86.tar.gz', graph['runnerRuntime']['runnerUrl'])
        self.assertEqual(plan['runtime']['runner'], 'pol-8.2')
        self.assertEqual(plan['runtime']['runnerVersion'], '8.2')
        self.assertEqual(plan['runtime']['runnerSource'], 'playonlinux-phoenicis-upstream')


if __name__ == '__main__':
    unittest.main()
