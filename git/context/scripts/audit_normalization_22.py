"""Read-only Sprint 22 planning audit; no corpus spectrum or experiment is run.

Extract only the named matrix constructors from their ASTs. Do not import or
execute the SDM pipeline, whose main routine can overwrite analysis outputs.
The small reference calculation is an audit oracle, not a production metric.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
from pathlib import Path

import numpy as np
import scipy
import scipy.sparse as sp


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def constructors(path, names):
    tree = ast.parse(Path(path).read_text(encoding="utf-8-sig"))
    selected = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names]
    assert {n.name for n in selected} == set(names)
    # Postponed annotations avoid importing NetworkX merely to supply type hints.
    body = [ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0)] + selected
    module = ast.fix_missing_locations(ast.Module(body=body, type_ignores=[]))
    scope = {"np": np, "sp": sp}
    exec(compile(module, str(path), "exec"), scope)
    return scope


def audit(root, sdm):
    ra = root / "git/RetrievalArena/retrieval_arena/graph/magnetic_phase.py"
    sdm_code = sdm / "docspectra-analysis/docspectra_analysis/magnetic_spectra.py"
    sdm_report = sdm / "analysis/python312/metrics.magnetic.json"
    ra_functions = constructors(ra, ["canonical_edges", "magnetic_matrix"])
    sdm_functions = constructors(sdm_code, ["build_magnetic_laplacian_sparse"])
    nodes = ["a", "b", "c", "d", "isolate"]
    edges = [("a", "b"), ("b", "c"), ("c", "d")]
    index = {v: i for i, v in enumerate(nodes)}

    class GraphFixture:
        def edges(self):
            return edges

    adjacency = np.zeros((5, 5))
    for u, v in edges:
        adjacency[index[u], index[v]] = 1
    weights = (adjacency + adjacency.T) / 2
    degrees = weights.sum(axis=1)
    inverse = np.zeros(5)
    inverse[degrees > 0] = degrees[degrees > 0] ** -0.5
    cases = []
    for q in [0.0, 0.25]:
        hermitian = weights * np.exp(2j * np.pi * q * (adjacency - adjacency.T))
        unnormalized = np.diag(degrees) - hermitian
        normalized = inverse[:, None] * unnormalized * inverse[None, :]
        old_ra = ra_functions["magnetic_matrix"](nodes, edges, q)
        old_sdm, sdm_degrees = sdm_functions["build_magnetic_laplacian_sparse"](GraphFixture(), index, q)
        old_sdm = old_sdm.toarray()
        np.testing.assert_allclose(old_ra, unnormalized, atol=1e-14)
        np.testing.assert_allclose(old_sdm, unnormalized, atol=1e-14)
        np.testing.assert_allclose(sdm_degrees, degrees, atol=1e-14)
        assert np.max(np.abs(old_ra - normalized)) > 0.4
        assert np.max(np.abs(old_sdm - normalized)) > 0.4
        cases.append({"q": q, "ra_unnormalized_max_error": float(np.max(np.abs(old_ra - unnormalized))),
                      "sdm_unnormalized_max_error": float(np.max(np.abs(old_sdm - unnormalized))),
                      "normalized_matrix_max_difference": float(np.max(np.abs(old_ra - normalized))),
                      "unnormalized_eigenvalues": np.linalg.eigvalsh(unnormalized).tolist(),
                      "normalized_reference_eigenvalues": np.linalg.eigvalsh(normalized).tolist(),
                      "normalized_isolate_diagonal": float(normalized[-1, -1].real)})
    evidence = root / "git/context/evidence"
    migration_file = evidence / "phase-flow-repair-v1-2026-10-06.json"
    anchors = []
    for row in read(migration_file)["migration"]:
        inputs = row["inputs"]
        path = Path(inputs["source_graph_csv"])
        actual = sha(path) if path.is_file() else None
        anchors.append({"corpus_id": row["corpus_id"], "path": str(path), "actual_sha256": actual,
                        "matches": actual == inputs["source_graph_csv_sha256"]})
    orientation_file = evidence / "orientation-spectra-inputs-v1-2026-10-06.json"
    orientations = read(orientation_file)
    orientation_checks = []
    for row in orientations["versions"]:
        for filename, key in [("graph_edges.csv", "edge_file_sha256"),
                              ("graph_edges_sensitivity.csv", "sensitivity_edge_file_sha256")]:
            path = root / orientations["artifact_root"] / "versions" / row["version_id"] / filename
            actual = sha(path) if path.is_file() else None
            orientation_checks.append({"version": row["version_id"], "file": filename,
                                       "actual_sha256": actual, "matches": actual == row[key]})
    analysis = root / "artifacts/docker-corpus-variants-v1/runs/wc-v1/analysis-18"
    inventory = {}
    for name in ["controls", "spectra/full", "subgraph"]:
        files = sorted((analysis / name).glob("*.json"))
        inventory[name] = [{"name": p.name, "sha256": sha(p)} for p in files]
    run_file = evidence / "wc-refinement-run-v1-2026-10-18.json"
    final_trees = {arm: {"path": row["final_tree_path"], "recorded_tree_id": row["final_tree_id"],
                          "directory_exists": Path(row["final_tree_path"]).is_dir(),
                          "tree_hash_recomputed": False}
                   for arm, row in read(run_file)["arms"].items()}
    sources = [ra, sdm_code, sdm_report, migration_file, orientation_file, run_file,
               root / "git/context/scripts/wc_spectra_18.py",
               root / "git/context/scripts/wc_refinement_15.py",
               root / "git/context/scripts/restructuring_runs.py",
               root / "git/context/scripts/orientation_spectra_09c.py",
               root / "git/context/scripts/structure_forcing_12.py"]
    old = read(sdm_report)
    return {"schema_version": "docspectra.normalization_planning_audit.v1", "date": "2026-10-25",
            "scope": "local source/asset audit and five-node constructor fixture; Sprint 22 not executed",
            "script_sha256": sha(__file__), "numpy": np.__version__, "scipy": scipy.__version__,
            "sources": [{"path": str(p), "sha256": sha(p)} for p in sources],
            "fixture": {"nodes": nodes, "edges": edges, "weighted_degrees": degrees.tolist(), "cases": cases},
            "sdm_python312_saved_report": {"normalize_metadata": old["config"]["normalize"],
                                           "lambda2_mag": old["lambda2_mag"],
                                           "exceeds_symmetric_normalized_bound_2": old["lambda2_mag"] > 2,
                                           "exact_historical_source_provenance_verified": False},
            "recovered_anchor_graph_checks": anchors, "orientation_edge_checks": orientation_checks,
            "refinement_file_inventory": inventory, "final_trees": final_trees,
            "limitations": ["No corpus spectra recomputed", "No full graph reconstruction or final-tree hash replay",
                            "No web/remote/model calls", "Current SDM source is not a pinned proof of its historical execution",
                            "Inventory confirms files, not complete node/view reconstruction"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, default=Path(__file__).resolve().parents[3])
    parser.add_argument("--sdm-repo", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    sdm = args.sdm_repo or args.workspace.parent / "DocSpectraSDM2026/git/DocSpectraSDM2026"
    result = audit(args.workspace, sdm)
    with args.output.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(result, stream, indent=2, sort_keys=True, allow_nan=False)
        stream.write("\n")
    print(json.dumps({"output": str(args.output), "sha256": sha(args.output),
                      "anchor_hashes_match": sum(r["matches"] for r in result["recovered_anchor_graph_checks"]),
                      "orientation_hashes_match": sum(r["matches"] for r in result["orientation_edge_checks"]),
                      "inventory_counts": {k: len(v) for k, v in result["refinement_file_inventory"].items()},
                      "fixture_cases_passed": len(result["fixture"]["cases"])}))


if __name__ == "__main__":
    main()
