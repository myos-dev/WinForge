"""Tests for the WinForge Container Manager and runtime catalog."""
from __future__ import annotations
import unittest
from container.manager import (
    list_definitions,
    get_image_ref,
    get_local_image_ref,
    build_container,
)


class ContainerManagerTests(unittest.TestCase):

    def test_list_definitions_returns_all_providers(self):
        defs = list_definitions()
        names = [d["name"] for d in defs]
        self.assertIn("wine", names)
        self.assertIn("staging", names)
        self.assertNotIn("proton", names)
        self.assertIn("umu-proton-ge", names)
        self.assertNotIn("proton-ge", names)

    def test_get_image_ref_known_provider_returns_published_ref(self):
        self.assertEqual(get_image_ref("wine", "9.0"),
                         "ghcr.io/myos-dev/winforge-wine:9.0")
        self.assertEqual(get_image_ref("staging", "9.0"),
                         "ghcr.io/myos-dev/winforge-wine-staging:9.0")
        self.assertEqual(get_image_ref("umu-proton-ge", "GE-Proton9-27"),
                         "ghcr.io/myos-dev/winforge-umu-proton-ge:GE-Proton9-27")

    def test_get_local_image_ref_known_provider(self):
        self.assertEqual(get_local_image_ref("wine", "9.0"),
                         "winforge/wine:9.0")
        self.assertEqual(get_local_image_ref("staging", "9.0"),
                         "winforge/wine-staging:9.0")

    def test_get_image_ref_unknown_falls_back_to_published_name(self):
        self.assertEqual(get_image_ref("unknown", "1.0"),
                         "ghcr.io/myos-dev/winforge-unknown:1.0")
        self.assertEqual(get_local_image_ref("unknown", "1.0"),
                         "winforge/unknown:1.0")

    def test_build_container_unknown_provider(self):
        result = build_container("nonexistent", "1.0")
        self.assertFalse(result.success)
        self.assertIn("Unknown provider/version", result.log)

    def test_build_container_no_docker(self):
        # Returns file-not-found or build-failed — not an exception
        result = build_container("wine", "9.0", build_cmd="nonexistent-docker")
        self.assertFalse(result.success)
        self.assertIn("not found", result.log.lower())


class RuntimeCatalogTests(unittest.TestCase):

    def test_catalog_ci_matrix_contains_build_entries(self):
        from runtime.catalog import ci_matrix
        matrix = ci_matrix()
        self.assertIn("include", matrix)
        providers = {entry["provider"] for entry in matrix["include"]}
        self.assertEqual(providers, {"wine", "staging", "umu-proton-ge"})
        for entry in matrix["include"]:
            self.assertIn("dockerfile", entry)
            self.assertIn("build_arg", entry)
            self.assertIn("image_name", entry)
            self.assertIsInstance(entry["version"], str)

    def test_catalog_default_version_resolution(self):
        from runtime.catalog import resolve_catalog_version
        entry = resolve_catalog_version("wine", "default")
        self.assertIsNotNone(entry)
        assert entry is not None
        self.assertEqual(entry.version, "9.0")
        self.assertEqual(entry.published_ref,
                         "ghcr.io/myos-dev/winforge-wine:9.0")

    def test_valve_proton_and_legacy_proton_ge_are_not_active_providers(self):
        from runtime.catalog import resolve_catalog_version
        self.assertIsNone(resolve_catalog_version("proton", "default"))
        self.assertIsNone(resolve_catalog_version("proton-ge", "default"))


class RuntimeProviderOCITests(unittest.TestCase):

    def test_runtime_binding_includes_published_and_local_oci_images(self):
        from core.manifest import RuntimeSpec
        from runtime.providers import resolve_runtime
        binding = resolve_runtime(RuntimeSpec(
            provider="wine", version="9.0",
        ))
        self.assertEqual(binding.oci_image,
                         "ghcr.io/myos-dev/winforge-wine:9.0")
        self.assertEqual(binding.local_oci_image,
                         "winforge/wine:9.0")
        self.assertTrue(binding.runtime_usable)

    def test_runtime_binding_oci_image_staging(self):
        from core.manifest import RuntimeSpec
        from runtime.providers import resolve_runtime
        binding = resolve_runtime(RuntimeSpec(
            provider="staging", version="9.0",
        ))
        self.assertEqual(binding.oci_image,
                         "ghcr.io/myos-dev/winforge-wine-staging:9.0")
        self.assertEqual(binding.local_oci_image,
                         "winforge/wine-staging:9.0")

    def test_runtime_binding_oci_image_umu_proton_ge(self):
        from core.manifest import RuntimeSpec
        from runtime.providers import resolve_runtime
        binding = resolve_runtime(RuntimeSpec(
            provider="umu-proton-ge", version="GE-Proton9-27",
        ))
        self.assertEqual(binding.oci_image,
                         "ghcr.io/myos-dev/winforge-umu-proton-ge:GE-Proton9-27")
        self.assertEqual(binding.local_oci_image,
                         "winforge/umu-proton-ge:GE-Proton9-27")
        self.assertEqual(binding.launcher, "umu")

    def test_oci_image_in_to_dict(self):
        from core.manifest import RuntimeSpec
        from runtime.providers import resolve_runtime
        binding = resolve_runtime(RuntimeSpec(
            provider="umu-proton-ge", version="GE-Proton9-27",
        ))
        d = binding.to_dict()
        self.assertIn("ociImage", d)
        self.assertIn("localOciImage", d)
        self.assertEqual(d["ociImage"],
                         "ghcr.io/myos-dev/winforge-umu-proton-ge:GE-Proton9-27")
        self.assertTrue(d["runtimeUsable"])

    def test_to_dict_omits_none_oci(self):
        """Custom providers without OCI mapping should omit the field."""
        from runtime.providers import register_provider, resolve_runtime
        from core.manifest import RuntimeSpec

        class CustomProvider:
            name = "custom-test"
            def resolve(self, spec):
                from runtime.providers import RuntimeBinding
                return RuntimeBinding(
                    spec.provider, spec.version, "wine",
                )

        register_provider(CustomProvider())
        binding = resolve_runtime(RuntimeSpec(
            provider="custom-test", version="1.0",
        ))
        d = binding.to_dict()
        self.assertIsNone(binding.oci_image)
        self.assertNotIn("ociImage", d)


if __name__ == "__main__":
    unittest.main()
