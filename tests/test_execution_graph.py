"""Tests for WinForge execution graph generation."""
from __future__ import annotations
import json
import tempfile
import unittest
from pathlib import Path

from artifact.bundle import create_bundle
from artifact.graph import build_execution_graph
from core.manifest import Manifest


VALID = {
    "schemaVersion": "winforge.dev/v0",
    "name": "sample",
    "version": "1.0.0",
    "runtime": {"provider": "wine", "version": "9.0"},
    "dependencies": [{"kind": "winetricks", "verbs": ["corefonts"]}],
    "install": [{
        "kind": "portable",
        "source": "file://app.zip",
        "target": "C:/Program Files/App",
    }],
    "filesystem": [{
        "source": "config.ini",
        "target": "C:/Program Files/App/config.ini",
    }],
    "launch": {
        "entrypoint": "C:/Program Files/App/App.exe",
        "args": ["--safe-mode"],
        "env": {"WINEDLLOVERRIDES": "mscoree,mshtml=disabled"},
        "workingDirectory": "C:/Program Files/App",
    },
    "provenance": {"sources": []},
}


class ExecutionGraphTests(unittest.TestCase):

    def test_graph_records_resolved_runtime_launch_graphics_and_compatibility(self):
        graph = build_execution_graph(Manifest.from_dict(VALID))

        self.assertEqual(graph["schemaVersion"], "winforge.execution-graph/v0")
        self.assertEqual(graph["application"], {"name": "sample", "version": "1.0.0"})
        self.assertEqual(graph["artifact"]["kind"], "winforge.bundle")
        self.assertEqual(graph["builderRuntime"]["provider"], "wine")
        self.assertEqual(graph["builderRuntime"]["version"], "9.0")
        self.assertEqual(graph["builderRuntime"]["image"], "ghcr.io/myos-dev/winforge-wine:9.0")
        self.assertNotIn("network", graph["builderRuntime"])
        self.assertEqual(graph["runnerRuntime"]["network"], "none")
        self.assertEqual(
            {k: v for k, v in graph["runnerRuntime"].items() if k != "network"},
            graph["builderRuntime"],
        )
        self.assertEqual(graph["graphics"]["defaultMode"], "headless")
        self.assertEqual(graph["graphics"]["supportedModes"], ["headless", "vnc"])
        self.assertEqual(graph["launch"]["entrypoint"], "C:/Program Files/App/App.exe")
        self.assertEqual(graph["launch"]["args"], ["--safe-mode"])
        self.assertTrue(graph["compatibility"]["requiresExactRuntime"])

    def test_graph_has_deterministic_phase_nodes_and_edges(self):
        first = build_execution_graph(Manifest.from_dict(VALID))
        second = build_execution_graph(Manifest.from_dict(VALID))
        self.assertEqual(first, second)

        node_ids = [node["id"] for node in first["nodes"]]
        self.assertIn("runtime:wine:9.0", node_ids)
        self.assertIn("phase:init-prefix", node_ids)
        self.assertIn("phase:seal-artifact", node_ids)
        self.assertIn("artifact:bundle", node_ids)

        edges = {(edge["from"], edge["to"], edge["type"]) for edge in first["edges"]}
        self.assertIn(("manifest:sample:1.0.0", "runtime:wine:9.0", "resolves"), edges)
        self.assertIn(("phase:validate", "phase:seal-artifact", "precedes"), edges)
        self.assertIn(("phase:seal-artifact", "artifact:bundle", "produces"), edges)

    def test_bundle_writes_metadata_graph_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            bundle = create_bundle(Manifest.from_dict(VALID), Path(tmp), dry_run=True)
            graph_path = bundle / "metadata" / "graph.json"
            self.assertTrue(graph_path.exists())
            graph = json.loads(graph_path.read_text(encoding="utf-8"))
            self.assertEqual(graph["schemaVersion"], "winforge.execution-graph/v0")
            self.assertEqual(graph["builderRuntime"]["image"], "ghcr.io/myos-dev/winforge-wine:9.0")


if __name__ == "__main__":
    unittest.main()
