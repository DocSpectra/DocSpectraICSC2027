"""Invariant tests for Sprint 09c orientation versions and statistics helpers."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import orientation_spectra_09c as m  # noqa: E402


class OrientationTests(unittest.TestCase):
    EDGES = [("a", "b"), ("b", "a"), ("b", "c"), ("c", "d"), ("d", "b"), ("a", "e"), ("e", "c")]

    def test_skeleton_and_reciprocity_preserved(self):
        reciprocal, oneway = m.skeleton(self.EDGES)
        self.assertEqual(reciprocal, [("a", "b")])
        base = {frozenset(e) for e in self.EDGES}
        for v in m.build_versions(reciprocal, oneway):
            es = set(v["edges"])
            self.assertEqual({frozenset(e) for e in es}, base)
            self.assertIn(("a", "b"), es)
            self.assertIn(("b", "a"), es)
            if v["version_id"] != "U":
                self.assertEqual(len(es), len(self.EDGES))

    def test_reversal_and_partial_counts(self):
        reciprocal, oneway = m.skeleton(self.EDGES)
        versions = {v["version_id"]: v for v in m.build_versions(reciprocal, oneway)}
        self.assertEqual(set(versions["R"]["edges"]), {("a", "b"), ("b", "a")} | {(t, s) for s, t in oneway})
        self.assertEqual(versions["P50-91050"]["flipped_one_way"], round(0.5 * len(oneway)))
        self.assertEqual(len(versions), 32)

    def test_reversal_preserves_magnetic_spectrum(self):
        import numpy as np
        mp = m.load_phase()
        nodes = sorted({x for e in self.EDGES for x in e})
        reciprocal, oneway = m.skeleton(self.EDGES)
        rev = m.orient(reciprocal, oneway, set(range(len(oneway))))
        for q in m.Q_VALUES:
            a = np.linalg.eigvalsh(mp.magnetic_matrix(nodes, self.EDGES, q))
            b = np.linalg.eigvalsh(mp.magnetic_matrix(nodes, rev, q))
            self.assertLess(float(np.max(abs(a - b))), 1e-12)

    def test_holm_and_spearman(self):
        self.assertEqual(m.holm({"x": 0.01, "y": 0.04}), {"x": 0.02, "y": 0.04})
        self.assertAlmostEqual(m.spearman([1, 2, 3], [3, 2, 1]), -1.0)
        self.assertIsNone(m.spearman([1, 1, 1], [1, 2, 3]))


if __name__ == "__main__":
    unittest.main()
