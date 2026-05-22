import os
import unittest
import tempfile
import json
from registry.metadata import init_db, save_artifact
from registry.resolver import Version, satisfies, resolve

class TestVersionAndConstraints(unittest.TestCase):
    def test_version_parsing(self):
        v = Version.parse("1.2.3-alpha.1+build.12")
        self.assertEqual(v.major, 1)
        self.assertEqual(v.minor, 2)
        self.assertEqual(v.patch, 3)
        self.assertEqual(v.prerelease, "alpha.1")
        self.assertEqual(v.build, "build.12")

    def test_version_comparison(self):
        self.assertTrue(Version.parse("1.2.3") > Version.parse("1.2.2"))
        self.assertTrue(Version.parse("1.10.0") > Version.parse("1.2.0"))
        # Pre-releases are lower priority
        self.assertTrue(Version.parse("1.0.0-alpha") < Version.parse("1.0.0"))
        self.assertTrue(Version.parse("1.0.0-alpha.1") < Version.parse("1.0.0-alpha.2"))
        # Build metadata is ignored for sorting
        self.assertTrue(Version.parse("1.0.0+build1") == Version.parse("1.0.0+build2"))

    def test_satisfies_exact(self):
        self.assertTrue(satisfies("1.2.3", "1.2.3"))
        self.assertTrue(satisfies("1.2.3", "=1.2.3"))
        self.assertFalse(satisfies("1.2.4", "1.2.3"))

    def test_satisfies_caret(self):
        self.assertTrue(satisfies("1.2.3", "^1.2.3"))
        self.assertTrue(satisfies("1.5.0", "^1.2.3"))
        self.assertFalse(satisfies("2.0.0", "^1.2.3"))
        # Caret on 0.x
        self.assertTrue(satisfies("0.2.5", "^0.2.3"))
        self.assertFalse(satisfies("0.3.0", "^0.2.3"))
        # Caret on 0.0.x
        self.assertTrue(satisfies("0.0.3", "^0.0.3"))
        self.assertFalse(satisfies("0.0.4", "^0.0.3"))

    def test_satisfies_tilde(self):
        self.assertTrue(satisfies("1.2.5", "~1.2.3"))
        self.assertFalse(satisfies("1.3.0", "~1.2.3"))
        self.assertTrue(satisfies("1.2.0", "~1.2"))
        self.assertTrue(satisfies("1.5.0", "~1"))

    def test_satisfies_ranges(self):
        self.assertTrue(satisfies("1.5.0", ">=1.0.0 <2.0.0"))
        self.assertFalse(satisfies("2.0.0", ">=1.0.0 <2.0.0"))
        self.assertTrue(satisfies("1.2.0", ">1.0.0 <=1.5.0"))


class TestDependencyResolver(unittest.TestCase):
    def setUp(self):
        self.db_fd, self.db_path = tempfile.mkstemp()
        init_db(self.db_path)

    def tearDown(self):
        os.close(self.db_fd)
        os.unlink(self.db_path)

    def test_simple_resolution(self):
        # Save a few artifacts
        save_artifact("lib-core", "1.0.0", "sha1", 100, [], "alice")
        save_artifact("lib-core", "1.1.0", "sha2", 100, [], "alice")
        save_artifact("lib-http", "1.0.0", "sha3", 200, [{"name": "lib-core", "version": "^1.0.0"}], "alice")

        # Resolve
        lock = resolve([{"name": "lib-http", "version": "1.0.0"}])
        self.assertEqual(lock["packages"]["lib-http"]["version"], "1.0.0")
        # should select the highest satisfying version (1.1.0)
        self.assertEqual(lock["packages"]["lib-core"]["version"], "1.1.0")

    def test_cycle_detection(self):
        save_artifact("a", "1.0.0", "sha_a", 100, [{"name": "b", "version": "1.0.0"}], "alice")
        save_artifact("b", "1.0.0", "sha_b", 100, [{"name": "a", "version": "1.0.0"}], "alice")

        with self.assertRaises(ValueError) as ctx:
            resolve([{"name": "a", "version": "1.0.0"}])
        self.assertIn("Dependency cycle detected", str(ctx.exception))

    def test_version_conflict(self):
        save_artifact("lib-core", "1.0.0", "sha1", 100, [], "alice")
        save_artifact("lib-core", "2.0.0", "sha2", 100, [], "alice")
        save_artifact("a", "1.0.0", "sha_a", 100, [{"name": "lib-core", "version": "^1.0.0"}], "alice")
        save_artifact("b", "1.0.0", "sha_b", 100, [{"name": "lib-core", "version": "^2.0.0"}], "alice")

        with self.assertRaises(ValueError) as ctx:
            resolve([
                {"name": "a", "version": "1.0.0"},
                {"name": "b", "version": "1.0.0"}
            ])
        self.assertIn("Version conflict for package 'lib-core'", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
