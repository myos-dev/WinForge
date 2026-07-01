"""Container entrypoint contract tests."""
from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class ContainerEntrypointTests(unittest.TestCase):
    def test_xvfb_entrypoint_uses_bash_when_it_enables_pipefail(self):
        entrypoint = ROOT / "container/common/xvfb-entrypoint.sh"
        text = entrypoint.read_text(encoding="utf-8")
        first_line = text.splitlines()[0]

        self.assertIn("pipefail", text)
        self.assertIn(
            "bash",
            first_line,
            "xvfb-entrypoint.sh uses set -o pipefail and must not run under /bin/sh",
        )


if __name__ == "__main__":
    unittest.main()
