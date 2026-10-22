import copy
import unittest

from holding.update_plan import InvalidUpdateManifest, UpdateManifest, plan_updates

A = "a" * 64
B = "b" * 64
C = "c" * 64
BASE = {
    "schema": "atlas-update-manifest/v1",
    "release": "v1.0.1",
    "channel": "stable",
    "artifacts": [
        {"path": "app/app.zip", "sha256": B, "size": 1_000_000,
         "patches": [{"from_sha256": A, "sha256": C, "size": 20_000}]},
        {"path": "data/guides.json", "sha256": A, "size": 9000, "patches": []},
    ],
}


class UpdatePlanTests(unittest.TestCase):
    def test_no_change_no_download(self):
        plans = plan_updates(UpdateManifest.parse(BASE), {"app/app.zip": B, "data/guides.json": A})
        self.assertEqual([a.mode for a in plans], ["keep", "keep"])
        self.assertEqual(sum(a.download_bytes for a in plans), 0)

    def test_select_patch_only_when_compatible(self):
        manifest = UpdateManifest.parse(BASE)
        self.assertEqual(plan_updates(manifest, {"app/app.zip": A})[0].mode, "patch")
        self.assertEqual(plan_updates(manifest, {"app/app.zip": C})[0].mode, "full")
        self.assertEqual(plan_updates(manifest, {})[0].download_sha256, B)

    def test_full_when_patch_not_smaller(self):
        mod = copy.deepcopy(BASE)
        mod["artifacts"][0]["patches"][0]["size"] = 999_000
        self.assertEqual(plan_updates(UpdateManifest.parse(mod), {"app/app.zip": A})[0].mode, "full")

    def test_invalid_paths_and_duplicate(self):
        for bad in ("../app", "C:\\temp", "/abs", "app//zip", "app/./x", "a:b"):
            mod = copy.deepcopy(BASE)
            mod["artifacts"][0]["path"] = bad
            with self.subTest(bad=bad), self.assertRaises(InvalidUpdateManifest):
                UpdateManifest.parse(mod)
        mod = copy.deepcopy(BASE)
        mod["artifacts"].append(mod["artifacts"][0])
        with self.assertRaises(InvalidUpdateManifest):
            UpdateManifest.parse(mod)

    def test_bad_digest_size_and_schema(self):
        for key, value in (("sha256", "notsha"), ("size", -1), ("size", True), ("size", 1 << 50)):
            mod = copy.deepcopy(BASE)
            mod["artifacts"][0][key] = value
            with self.subTest(key=key, value=value), self.assertRaises(InvalidUpdateManifest):
                UpdateManifest.parse(mod)
        mod = copy.deepcopy(BASE)
        mod["schema"] = "v0"
        with self.assertRaises(InvalidUpdateManifest):
            UpdateManifest.parse(mod)

    def test_patch_base_duplicates_rejected(self):
        mod = copy.deepcopy(BASE)
        mod["artifacts"][0]["patches"].append(mod["artifacts"][0]["patches"][0])
        with self.assertRaises(InvalidUpdateManifest):
            UpdateManifest.parse(mod)

    def test_no_install_mutation(self):
        installed = {"app/app.zip": A}
        before = dict(installed)
        plan_updates(UpdateManifest.parse(BASE), installed)
        self.assertEqual(installed, before)


if __name__ == "__main__":
    unittest.main()
