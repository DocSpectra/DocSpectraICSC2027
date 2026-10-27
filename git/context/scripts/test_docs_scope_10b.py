"""Sprint 10b re-scope harness: deterministic, and identical to 09c when nothing is excluded.

Reads the saved 09c artifacts (never writes them); temporary roots go under ignored
artifacts/ and are removed. No model, embedding, network or container.
"""
import json
import shutil
import unittest
import uuid

import docs_scope_10b as ds

READY = (ds.O9C / "exposure/exposure.json").is_file() and (ds.O9C / "semantic/semantic.json").is_file()


@unittest.skipUnless(READY, "saved 09c artifacts absent")
class Rescope(unittest.TestCase):
    def setUp(self):
        self.base = ds.ROOT / "artifacts" / ("docs-scope-test-" + uuid.uuid4().hex)
        self.o9 = ds.load_09c()
        self.original_hashes = {p.name: ds.dcv.file_hash(p) for p in sorted((ds.O9C / "exposure").glob("*"))}

    def tearDown(self):
        if (ds.ROOT / "artifacts").resolve() not in self.base.resolve().parents:
            raise RuntimeError("unsafe-test-cleanup")
        shutil.rmtree(self.base, ignore_errors=True)
        # the original 09c exposure files were only read
        self.assertEqual({p.name: ds.dcv.file_hash(p) for p in sorted((ds.O9C / "exposure").glob("*"))},
                         self.original_hashes)

    def run_exposure(self, name, drop):
        dest = self.base / name
        report = ds.build_root(dest, drop, self.o9)
        ds.frozen_stage(self.o9, dest, "exposure")
        return dest, report

    def test_no_exclusions_reproduce_09c_exposure_bytes(self):
        dest, report = self.run_exposure("none", set())
        self.assertEqual(report["questions_with_empty_support"], [])
        self.assertTrue(report["lwcc_identical"])
        names = sorted(p.name for p in (ds.O9C / "exposure").glob("*.jsonl"))
        self.assertEqual(len(names), 64)
        for name in names:
            self.assertEqual((dest / "exposure" / name).read_bytes(), (ds.O9C / "exposure" / name).read_bytes(), name)
        mine, original = (json.loads((r / "exposure/exposure.json").read_text(encoding="utf-8")) for r in (dest, ds.O9C))
        self.assertEqual(mine["summary"], original["summary"])
        self.assertEqual(mine["analysis_plan_sha256"], original["analysis_plan_sha256"])

    def test_restricted_replay_is_deterministic_and_drops_only_excluded_ids(self):
        drop = {"static_googlecbe7fee896be512c__5ffd072f", "static_google161104f9fdea6089__e7905727"}
        a, report = self.run_exposure("a", drop)
        b, _ = self.run_exposure("b", drop)
        self.assertEqual((a / "exposure/exposure.json").read_bytes(), (b / "exposure/exposure.json").read_bytes())
        self.assertEqual(report["nodes"], 1261)
        self.assertTrue(report["lwcc_identical"])
        for path in (a / "exposure").glob("*.jsonl"):
            for line in path.read_text(encoding="utf-8").splitlines():
                row = json.loads(line)
                self.assertFalse(drop & set(row["selected"] + row["fallback"] + [row["seed"]]), path.name)
        with self.assertRaisesRegex(SystemExit, "output-root-exists"):
            ds.build_root(a, drop, self.o9)
        with self.assertRaisesRegex(SystemExit, "original-09c-root|output-root-exists"):
            ds.build_root(ds.O9C, drop, self.o9)


if __name__ == "__main__":
    unittest.main()
