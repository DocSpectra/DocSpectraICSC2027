"""Sprint 09c: directed spectra under controlled link re-orientation.

Stages, each writing fresh files under the ignored artifact root:

  freeze     inputs, orientation versions, question/support sets, answer-set
             selection; writes compact input evidence (no exposure computed)
  plan       writes the frozen analysis plan with its hash (before exposure)
  semantic   container stage: full MiniLM semantic ranking per question
  measures   spectral measures and directed-degree controls per version
  exposure   fixed-seed E009 outgoing expansion replay per version
  analyze    frozen tests, contrasts and sensitivity

Only historical, automatic candidate support (lexical top_docs) is used as an
exposure target. No model is called except the locked MiniLM embedder.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.util
import json
import os
import platform
import sys
import time
from collections import Counter, defaultdict, deque
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
CONTEXT = ROOT / "git/context"
RA = ROOT / "git/RetrievalArena"
DATASET = ROOT / "artifacts/docker-cikm-2026-05-14/dataset"
AUDIT = ROOT / "artifacts/docker-cikm-2026-05-14/audit"
LOCK = CONTEXT / "evidence/docker-cikm.lock.json"
SELECTION_V3 = CONTEXT / "evidence/e2e-development-selection-v3-2026-09-26.json"
RUNNER = RA / "tests/rag_semantic_graph_rerank/runner.py"
RUNNER_SHA256 = "df171fce2b3f855a00fb7f136a9007690c8db9179d4d0225e8402dff562ece7f"
IMAGE = "sha256:41b1789256a4a91e2e0d83e0d3bc69aa606a4b85e0299e9e59ab8a73bfc43a85"
INPUT_EVIDENCE = CONTEXT / "evidence/orientation-spectra-inputs-v1-2026-10-06.json"
PLAN_EVIDENCE = CONTEXT / "evidence/orientation-spectra-analysis-plan-v1-2026-10-06.json"
SCHEMA = "docspectra.icsc.orientation_spectra.v1"

# E009 directional pilot parameters, copied from the Sept 25 technical matrix
# (evidence/e2e-pilot-matrix-provenance-2026-09-25.json); not tuned here.
E009 = {"seed_top_k": 1, "graph_hops": 1, "neighbor_budget": 20, "candidate_budget": 40,
        "final_top_k": 4, "distance_penalty": 0.02, "max_context_chars_per_doc": 1200}
RANDOM_SEEDS = list(range(90901, 90921))
PARTIAL = {0.10: [91010, 91011, 91012], 0.25: [91025, 91026, 91027], 0.50: [91050, 91051, 91052]}
Q_VALUES = [0.05, 0.10, 0.15, 0.20, 0.25]
ANSWER_SET_SALT = "icsc2027-09c-answer-set-v1:"
ANSWER_SET_SIZE = 20


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def canon(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def write_new(path: Path, data) -> str:
    if path.exists():
        raise SystemExit(f"refusing-to-overwrite:{path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(data, indent=1, sort_keys=True, allow_nan=False) + "\n"
    path.write_text(text, encoding="utf-8", newline="\n")
    return sha256_file(path)


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def load_runner():
    if sha256_file(RUNNER) != RUNNER_SHA256:
        raise SystemExit("runner-identity-mismatch")
    spec = importlib.util.spec_from_file_location("e009_runner", RUNNER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def read_edges_csv(path: Path) -> list[tuple[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return [(row["source"], row["target"]) for row in csv.DictReader(handle)]


def edges_csv_bytes(edges) -> bytes:
    lines = ["source,target"] + [f"{s},{t}" for s, t in sorted(set(edges))]
    return ("\n".join(lines) + "\n").encode("utf-8")


def weak_components(nodes, edges):
    adj = defaultdict(set)
    for s, t in edges:
        adj[s].add(t)
        adj[t].add(s)
    seen, comps = set(), []
    for start in sorted(nodes):
        if start in seen:
            continue
        comp, queue = {start}, deque([start])
        seen.add(start)
        while queue:
            x = queue.popleft()
            for y in adj[x]:
                if y not in seen:
                    seen.add(y)
                    comp.add(y)
                    queue.append(y)
        comps.append(comp)
    comps.sort(key=lambda c: (-len(c), min(c)))
    return comps


def skeleton(edges):
    """Split directed simple edges into reciprocal pairs and one-way links."""
    es = {(s, t) for s, t in edges if s != t}
    reciprocal, oneway = set(), []
    for s, t in sorted(es):
        if (t, s) in es:
            reciprocal.add((min(s, t), max(s, t)))
        else:
            oneway.append((s, t))
    return sorted(reciprocal), oneway


def orient(reciprocal, oneway, flips: set[int]):
    out = []
    for a, b in reciprocal:
        out += [(a, b), (b, a)]
    for i, (s, t) in enumerate(oneway):
        out.append((t, s) if i in flips else (s, t))
    return sorted(out)


def build_versions(reciprocal, oneway):
    import numpy as np
    n = len(oneway)
    versions = [("F", "as_written", None, set()), ("R", "all_one_way_reversed", None, set(range(n)))]
    for seed in RANDOM_SEEDS:
        rng = np.random.default_rng(seed)
        flips = {i for i, f in enumerate(rng.random(n) < 0.5) if f}
        versions.append((f"RND{seed}", "random_half", seed, flips))
    for fraction, seeds in PARTIAL.items():
        k = int(round(fraction * n))
        for seed in seeds:
            rng = np.random.default_rng(seed)
            flips = {int(i) for i in rng.choice(n, size=k, replace=False)}
            versions.append((f"P{int(fraction * 100):02d}-{seed}", f"partial_{fraction:.2f}", seed, flips))
    built = []
    for vid, family, seed, flips in versions:
        built.append({"version_id": vid, "family": family, "seed": seed, "flipped_one_way": len(flips),
                      "edges": orient(reciprocal, oneway, flips)})
    two_way = sorted({(a, b) for s, t in oneway for a, b in ((s, t), (t, s))} |
                     {e for a, b in reciprocal for e in ((a, b), (b, a))})
    built.append({"version_id": "U", "family": "two_way", "seed": None, "flipped_one_way": None, "edges": two_way})
    return built


def source_paths():
    return {row["doc_id"]: row["source_path"] for row in read_json(AUDIT / "source-pages.json")}


def scaffold_pages(nodes, paths):
    """Navigation scaffolding for the sensitivity: _index pages and release notes."""
    out = {}
    for doc in nodes:
        path = paths[doc].lower()
        name = path.rsplit("/", 1)[-1]
        if name.startswith("_index."):
            out[doc] = "underscore_index"
        elif "release-notes" in path or "release_notes" in path:
            out[doc] = "release_notes"
    return out


def freeze(args):
    out = args.output.resolve()
    if out.exists():
        raise SystemExit("output-root-exists")
    lock = read_json(LOCK)
    for name, expected in lock["dataset_files"].items():
        if name in ("graph_edges.csv", "questions.jsonl", "faq_support_audit.jsonl") and sha256_file(DATASET / name) != expected:
            raise SystemExit(f"dataset-hash-mismatch:{name}")
    load_runner()
    nodes = sorted(p.stem for p in (DATASET / "corpus").glob("*.md"))
    if len(nodes) != lock["counts"]["retrieval_documents"]:
        raise SystemExit("corpus-count-mismatch")
    edges = read_edges_csv(DATASET / "graph_edges.csv")
    if len(edges) != len(set(edges)) or len(edges) != lock["counts"]["directed_edges"]:
        raise SystemExit("edge-count-mismatch")
    reciprocal, oneway = skeleton(edges)
    comps = weak_components(nodes, edges)
    lwcc = sorted(comps[0])
    paths = source_paths()
    scaffold = scaffold_pages(nodes, paths)

    eligible = lock["questions"]["eligible_historical_supported_ids"]
    partial = set(lock["questions"].get("excluded_partial_ids", ["docker_docs_faq_0003", "docker_docs_faq_0042"]))
    audit = {r["question_id"]: r for r in read_jsonl(DATASET / "faq_support_audit.jsonl")}
    if len(eligible) != 142 or partial & set(eligible):
        raise SystemExit("question-set-mismatch")
    questions = {r["question_id"]: r["question"] for r in read_jsonl(DATASET / "questions.jsonl")}
    support = {}
    for qid in eligible:
        row = audit[qid]
        if row["support_label"] != "supported":
            raise SystemExit(f"unsupported:{qid}")
        support[qid] = [d["doc_id"] for d in row["top_docs"]]
        if any(d not in set(nodes) for d in support[qid]):
            raise SystemExit(f"support-outside-corpus:{qid}")

    versions = build_versions(reciprocal, oneway)
    vdir = out / "versions"
    records = []
    for v in versions:
        data = edges_csv_bytes(v["edges"])
        d = vdir / v["version_id"]
        d.mkdir(parents=True)
        (d / "graph_edges.csv").write_bytes(data)
        sens = [(s, t) for s, t in v["edges"] if s not in scaffold and t not in scaffold]
        sdata = edges_csv_bytes(sens)
        (d / "graph_edges_sensitivity.csv").write_bytes(sdata)
        undirected = {(min(s, t), max(s, t)) for s, t in v["edges"]}
        records.append({"version_id": v["version_id"], "family": v["family"], "seed": v["seed"],
                        "flipped_one_way": v["flipped_one_way"], "directed_edges": len(v["edges"]),
                        "skeleton_pairs": len(undirected),
                        "edge_file_sha256": sha256_bytes(data),
                        "sensitivity_edges": len(sens), "sensitivity_edge_file_sha256": sha256_bytes(sdata)})
    f_hash = records[0]["edge_file_sha256"]
    if f_hash != sha256_bytes(edges_csv_bytes(edges)):
        raise SystemExit("F-version-not-identical-to-pinned-edges")

    # Answer-set selection (step 7), recorded before any exposure is computed.
    v3 = read_json(SELECTION_V3)
    excluded = set(v3["main_exclusion_ids"])
    pool = [q for q in eligible if q not in excluded]
    ranked = sorted(pool, key=lambda q: sha256_bytes((ANSWER_SET_SALT + q).encode()))
    answer_set = ranked[:ANSWER_SET_SIZE]

    staged = out / "retrieval-input"
    (staged / "corpus").mkdir(parents=True)
    for doc in nodes:
        (staged / "corpus" / f"{doc}.md").write_bytes((DATASET / "corpus" / f"{doc}.md").read_bytes())
    qlines = "".join(json.dumps({"question": questions[q], "question_id": q}, sort_keys=True) + "\n" for q in eligible)
    (staged / "questions.jsonl").write_text(qlines, encoding="utf-8", newline="\n")
    (staged / "graph_edges.csv").write_bytes(edges_csv_bytes(edges))

    evidence = {
        "schema_version": SCHEMA + ".inputs", "status": "frozen_before_measures_and_exposure",
        "frozen_at": datetime.now().astimezone().isoformat(),
        "dataset_lock_sha256": sha256_file(LOCK),
        "dataset_files": {n: sha256_file(DATASET / n) for n in ("graph_edges.csv", "questions.jsonl", "faq_support_audit.jsonl")},
        "corpus_inventory_sha256": lock["corpus_inventory_sha256"],
        "source_commit": lock["source"]["commit"],
        "e009": {"runner_sha256": RUNNER_SHA256, "image": IMAGE, "policy": "semantic_seeded_directional_pilot_v1",
                 "direction": "outgoing", "parameters": E009,
                 "parameter_source": "evidence/e2e-pilot-matrix-provenance-2026-09-25.json"},
        "embedding": {"model": lock["model"]["model_id"], "revision": lock["model"]["revision"],
                      "lock_sha256": lock["model"]["lock_sha256"]},
        "graph": {"nodes": len(nodes), "directed_edges": len(edges), "reciprocal_pairs": len(reciprocal),
                  "one_way_links": len(oneway), "skeleton_pairs": len(reciprocal) + len(oneway),
                  "weak_components": len(comps), "lwcc_nodes": len(lwcc),
                  "lwcc_node_list_sha256": sha256_bytes(canon(lwcc)),
                  "views": {"retrieval_no_faq": "all 1263 retrieval pages; the edge set E009 traverses",
                            "retrieval_no_faq_lwcc": "largest weak component of the fixed skeleton; primary spectral view"}},
        "sensitivity": {"rule": "drop every edge incident to a page whose source file is named _index.* or whose path contains release-notes; nodes retained",
                        "excluded_pages": len(scaffold), "by_kind": dict(Counter(scaffold.values())),
                        "excluded_page_list_sha256": sha256_bytes(canon(sorted(scaffold)))},
        "orientation_rules": {"reciprocal_pairs": "kept reciprocal in every version",
                              "random": "each one-way link independently reversed with probability 1/2, numpy default_rng(seed).random(n) < .5 over the sorted one-way list",
                              "partial": "exactly round(f*n) one-way links of F reversed, numpy default_rng(seed).choice(n, k, replace=False)",
                              "R": "every one-way link reversed", "U": "both directions of every skeleton pair"},
        "versions": records,
        "questions": {"count": len(eligible), "ids_sha256": sha256_bytes(canon(eligible)),
                      "excluded_partial_ids": sorted(partial),
                      "support_definition": "automatic candidate support: the five historical lexical top_docs of faq_support_audit.jsonl; not qrels",
                      "support_sha256": sha256_bytes(canon(support))},
        "answer_set": {"rule": f"sha256('{ANSWER_SET_SALT}' + question_id) ascending over the 142 eligible IDs minus the 30 v3 proposed main exclusions; first {ANSWER_SET_SIZE}",
                       "pool_size": len(pool), "selection_v3_sha256": sha256_file(SELECTION_V3),
                       "question_ids": answer_set, "selected_before_exposure": True},
        "artifact_root": str(out.relative_to(ROOT)).replace("\\", "/"),
        "staged_retrieval_input": {"files": ["questions.jsonl", "corpus/", "graph_edges.csv"],
                                   "questions_sha256": sha256_file(staged / "questions.jsonl"),
                                   "reference_assets_present": False},
    }
    (out / "support.json").write_text(json.dumps(support, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    (out / "scaffold-pages.json").write_text(json.dumps(scaffold, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    (out / "lwcc-nodes.json").write_text(json.dumps(lwcc) + "\n", encoding="utf-8")
    digest = write_new(args.evidence, evidence)
    print(json.dumps({"input_evidence_sha256": digest, "versions": len(records), "one_way": len(oneway),
                      "reciprocal_pairs": len(reciprocal), "scaffold": dict(Counter(scaffold.values())),
                      "answer_set": answer_set[:3]}, indent=1))


PHASE_CODE = RA / "retrieval_arena/graph/magnetic_phase.py"
PHASE_CODE_SHA256 = "d937edc46d75ac6094e5a8f21ddb0eb3156956adb73232b85aaced7d410a65fe"
PHASE_CONFIG = {"q": 0.25, "modes": 10, "gap_relative": 1e-08, "amplitude_relative": 1e-08, "phase_absolute": 1e-07,
                "residual_relative": 1e-10, "orthogonality_absolute": 1e-10, "minimum_coverage": 0.95}
PHASE_CONFIG_SHA256 = "b22cebd5d92dd08fec6ea7a0afa31ca9335847b38a20988d08d95d321d1044be"


def check_plan(root: Path):
    expected = (root / "analysis-plan.sha256").read_text(encoding="utf-8").split()[0]
    if sha256_file(PLAN_EVIDENCE) != expected:
        raise SystemExit("analysis-plan-changed-after-freeze")
    inputs = read_json(PLAN_EVIDENCE)["input_evidence_sha256"]
    if sha256_file(INPUT_EVIDENCE) != inputs:
        raise SystemExit("input-evidence-changed-after-freeze")
    return expected


def load_phase():
    if sha256_file(PHASE_CODE) != PHASE_CODE_SHA256:
        raise SystemExit("phase-code-identity-mismatch")
    sys.path.insert(0, str(RA))
    from retrieval_arena.graph import magnetic_phase as mp
    if mp.digest(PHASE_CONFIG) != PHASE_CONFIG_SHA256:
        raise SystemExit("phase-config-identity-mismatch")
    return mp


def gini(values):
    xs = sorted(values)
    n, total = len(xs), sum(xs)
    if n == 0 or total == 0:
        return 0.0
    return sum((2 * (i + 1) - n - 1) * x for i, x in enumerate(xs)) / (n * total)


def degree_controls(nodes, edges):
    out, inc = Counter(s for s, _ in edges), Counter(t for _, t in edges)
    return {"nodes": len(nodes), "edges": len(edges),
            "outdegree_gini": gini([out[x] for x in nodes]),
            "indegree_gini": gini([inc[x] for x in nodes]),
            "sources": sum(out[x] > 0 and inc[x] == 0 for x in nodes),
            "sinks": sum(inc[x] > 0 and out[x] == 0 for x in nodes),
            "isolated": sum(out[x] == 0 and inc[x] == 0 for x in nodes),
            "zero_outdegree": sum(out[x] == 0 for x in nodes),
            "total_degree_sha256": sha256_bytes(canon([out[x] + inc[x] for x in nodes]))}


def normalized_lambda2(nodes, edges):
    import numpy as np
    idx = {x: i for i, x in enumerate(nodes)}
    a = np.zeros((len(nodes), len(nodes)))
    for s, t in edges:
        a[idx[s], idx[t]] = a[idx[t], idx[s]] = 1
    d = a.sum(axis=1)
    inv = np.zeros(len(nodes))
    inv[d > 0] = 1 / np.sqrt(d[d > 0])
    lap = np.diag((d > 0).astype(float)) - inv[:, None] * a * inv[None, :]
    return float(np.linalg.eigvalsh(lap)[1])


def spectra(mp, nodes, edges, qs, *, subspace: bool):
    import numpy as np
    es = mp.canonical_edges(nodes, edges)
    rec = {}
    for q in qs:
        lap = mp.magnetic_matrix(nodes, es, q)
        e, v = np.linalg.eigh(lap)
        rec[f"{q:.2f}"] = {"lambda1": float(e[0]), "lambda2": float(e[1]), "lambda3": float(e[2]),
                           "residual_relative": float(np.linalg.norm(lap @ v - v * e) / max(1.0, np.linalg.norm(lap)))}
        if subspace and abs(q - 0.25) < 1e-12:
            r = mp.measure_subspace(nodes, es, mp.PhaseConfig(**PHASE_CONFIG), eigenpairs=(e, v))
            rec["subspace_polarization_v1"] = {k: r.get(k) for k in ("status", "value", "actual_modes", "cutoff_expansion",
                                                                       "validity_reasons", "full_spectrum_selected")}
    return rec


def measures(args):
    os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
    root = args.root.resolve()
    plan_sha = check_plan(root)
    mp = load_phase()
    import numpy as np
    inputs = read_json(INPUT_EVIDENCE)
    nodes = sorted(p.stem for p in (root / "retrieval-input/corpus").glob("*.md"))
    lwcc = read_json(root / "lwcc-nodes.json")
    f_sens = read_edges_csv(root / "versions/F/graph_edges_sensitivity.csv")
    lwcc_sens = sorted(weak_components(nodes, f_sens)[0])
    out_dir = root / "measures"
    out_dir.mkdir()
    rows = []
    t0 = time.time()
    for rec in inputs["versions"]:
        vid = rec["version_id"]
        vdir = root / "versions" / vid
        row = {"version_id": vid, "family": rec["family"]}
        for tag, fname, lw in (("main", "graph_edges.csv", lwcc), ("sensitivity", "graph_edges_sensitivity.csv", lwcc_sens)):
            if sha256_file(vdir / fname) != rec["edge_file_sha256" if tag == "main" else "sensitivity_edge_file_sha256"]:
                raise SystemExit(f"edge-file-hash-mismatch:{vid}:{tag}")
            edges = read_edges_csv(vdir / fname)
            lset = set(lw)
            ledges = [(s, t) for s, t in edges if s in lset and t in lset]
            row[tag] = {
                "lwcc_nodes": len(lw),
                "lwcc": {"spectra": spectra(mp, lw, ledges, [0.0] + Q_VALUES, subspace=True),
                         "controls": degree_controls(lw, ledges), "normalized_undirected_lambda2": normalized_lambda2(lw, ledges)},
                "retrieval": {"spectra": spectra(mp, nodes, edges, [0.0, 0.25], subspace=False),
                              "controls": degree_controls(nodes, edges)},
            }
        rows.append(row)
        print(vid, f"{time.time() - t0:.1f}s", row["main"]["lwcc"]["spectra"]["0.25"]["lambda1"], flush=True)
    result = {"schema_version": SCHEMA + ".measures", "analysis_plan_sha256": plan_sha,
              "input_evidence_sha256": sha256_file(INPUT_EVIDENCE),
              "phase_code_sha256": PHASE_CODE_SHA256, "phase_config_sha256": PHASE_CONFIG_SHA256,
              "lwcc_sensitivity_nodes": len(lwcc_sens), "lwcc_sensitivity_sha256": sha256_bytes(canon(lwcc_sens)),
              "runtime": {"python": sys.version.split()[0], "numpy": np.__version__, "platform": platform.platform(),
                          "OPENBLAS_NUM_THREADS": os.environ.get("OPENBLAS_NUM_THREADS"),
                          "OMP_NUM_THREADS": os.environ.get("OMP_NUM_THREADS"), "solver": "numpy.linalg.eigh full, complex128",
                          "wall_seconds": time.time() - t0},
              "versions": rows}
    print(json.dumps({"measures_sha256": write_new(out_dir / "measures.json", result)}))


MODEL_DIR = ROOT / "artifacts/docker-cikm-2026-05-14/model"
PROVENANCE_DIR = ROOT / "artifacts/docker-cikm-2026-05-14/provenance"
SEMANTIC_SCRIPT = CONTEXT / "scripts/orientation_spectra_09c_semantic.py"
CONFIG_BASE = {"batch_size": 32, "embedding_backend": "sentence_transformers_local",
               "model_cache_path": "/models/minilm", "model_id": "sentence-transformers/all-MiniLM-L6-v2",
               "model_lock_path": "/model_provenance/embedding_models.lock.json",
               "model_revision": "c9745ed1d9f207416be6d2e6f8de32d1f16199bf", "use_pagerank_prior": False}


def stage_input(root: Path, name: str, edge_file: Path, config: dict) -> Path:
    import shutil
    target = root / "container-inputs" / name
    if target.exists():
        raise SystemExit(f"input-exists:{name}")
    shutil.copytree(root / "retrieval-input" / "corpus", target / "corpus")
    shutil.copy2(root / "retrieval-input" / "questions.jsonl", target / "questions.jsonl")
    shutil.copy2(edge_file, target / "graph_edges.csv")
    (target / "config.yaml").write_text(json.dumps(config, sort_keys=True), encoding="utf-8")
    return target


def docker_run(input_dir: Path, output_dir: Path, *, script: bool, log: Path):
    import subprocess
    output_dir.mkdir(parents=True)
    cmd = ["docker", "run", "--rm", "--network", "none", "--cpus", "4", "--memory", "8g",
           "--mount", f"type=bind,src={input_dir},dst=/input,readonly",
           "--mount", f"type=bind,src={output_dir},dst=/output",
           "--mount", f"type=bind,src={MODEL_DIR},dst=/models/minilm,readonly",
           "--mount", f"type=bind,src={PROVENANCE_DIR},dst=/model_provenance,readonly"]
    if script:
        cmd += ["--mount", f"type=bind,src={RUNNER},dst=/work/runner.py,readonly",
                "--mount", f"type=bind,src={SEMANTIC_SCRIPT},dst=/work/semantic.py,readonly",
                "--entrypoint", "python", IMAGE, "/work/semantic.py"]
    else:
        cmd += [IMAGE]
    t0 = time.time()
    proc = subprocess.run(cmd, capture_output=True, text=True)
    with log.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"command": cmd, "returncode": proc.returncode, "seconds": time.time() - t0,
                                 "stdout_tail": proc.stdout[-2000:], "stderr_tail": proc.stderr[-4000:]}) + "\n")
    if proc.returncode:
        raise SystemExit(f"docker-failed:{output_dir.name}:{proc.stderr[-800:]}")


def semantic(args):
    root = args.root.resolve()
    check_plan(root)
    config = dict(CONFIG_BASE, **E009, pilot_direction="outgoing", variant_id="icsc09c_semantic")
    inp = stage_input(root, "semantic", root / "versions/F/graph_edges.csv", config)
    out = root / "semantic"
    docker_run(inp, out, script=True, log=root / "docker-commands.jsonl")
    data = read_json(out / "semantic.json")
    if data["runner_sha256"] != RUNNER_SHA256 or len(data["rankings"]) != 142:
        raise SystemExit("semantic-output-invalid")
    print(json.dumps({"semantic_sha256": sha256_file(out / "semantic.json"), "dims": data["dimensions"]}))


def edge_map(path: Path, nodes: set[str], *, mode: str):
    edges = defaultdict(set)
    for s, t in read_edges_csv(path):
        if s in nodes and t in nodes and s != t:
            if mode == "reversed":
                s, t = t, s
            edges[s].add(t)
            if mode == "undirected":
                edges[t].add(s)
    return edges


def replay(runner, rankings, edges, mode: str):
    out = {}
    for qid, ranked in rankings.items():
        ranked = [(d, s) for d, s in ranked]
        sel = runner.pilot_selection(ranked, edges, direction=mode, seed_top_k=E009["seed_top_k"],
                                     hop_budget=E009["graph_hops"], neighbor_budget=E009["neighbor_budget"],
                                     candidate_budget=E009["candidate_budget"], final_top_k=E009["final_top_k"],
                                     distance_penalty=E009["distance_penalty"])
        out[qid] = {"seeds": sel["seeds"], "selected": sel["selected"], "fallback": sel["fallback"],
                    "candidates": sorted(sel["candidates"]), "expanded": sel["expanded"]}
    return out


def verify_e009(args):
    """Admission check: host replay equals the pinned E009 image on five runs."""
    root = args.root.resolve()
    plan_sha = check_plan(root)
    runner = load_runner()
    rankings = read_json(root / "semantic/semantic.json")["rankings"]
    nodes = {p.stem for p in (root / "retrieval-input/corpus").glob("*.md")}
    runs = [("F-outgoing", "F", "outgoing"), ("F-reversed", "F", "reversed"), ("R-outgoing", "R", "outgoing"),
            ("U-outgoing", "U", "outgoing"), ("F-undirected", "F", "undirected")]
    report = {"schema_version": SCHEMA + ".e009_verification", "analysis_plan_sha256": plan_sha, "runs": {}}
    host = {}
    for name, vid, mode in runs:
        config = dict(CONFIG_BASE, **E009, pilot_direction=mode, directed=mode != "undirected",
                      variant_id=f"icsc09c_{name}")
        edge_file = root / "versions" / vid / "graph_edges.csv"
        inp = stage_input(root, name, edge_file, config)
        out = root / "e009-verification" / name
        docker_run(inp, out, script=False, log=root / "docker-commands.jsonl")
        preds = {r["question_id"]: r["semantic_graph_rerank"] for r in read_jsonl(out / "predictions.jsonl")}
        mine = replay(runner, rankings, edge_map(edge_file, nodes, mode=mode), mode)
        host[name] = mine
        mismatches = [q for q in rankings if (preds[q]["final_context_doc_ids"], preds[q]["seed_doc_ids"],
                                              preds[q]["candidate_doc_ids"], preds[q]["fallback_doc_ids"]) !=
                      (mine[q]["selected"], mine[q]["seeds"], mine[q]["candidates"], mine[q]["fallback"])]
        report["runs"][name] = {"version": vid, "mode": mode, "edge_file_sha256": sha256_file(edge_file),
                                "predictions_sha256": sha256_file(out / "predictions.jsonl"),
                                "metadata_sha256": sha256_file(out / "metadata.json"),
                                "questions": len(preds), "host_replay_mismatches": len(mismatches),
                                "mismatch_ids": mismatches[:10]}
    same = [q for q in rankings if host["R-outgoing"][q] == host["F-reversed"][q]]
    report["R_file_equals_builtin_reversed"] = len(same) == len(rankings)
    report["U_file_equals_builtin_undirected"] = all(host["U-outgoing"][q] == host["F-undirected"][q] for q in rankings)
    report["admitted"] = (all(r["host_replay_mismatches"] == 0 for r in report["runs"].values())
                          and report["R_file_equals_builtin_reversed"])
    print(json.dumps({"verification_sha256": write_new(root / "e009-verification/verification.json", report),
                      "admitted": report["admitted"], "R_equals_reversed": report["R_file_equals_builtin_reversed"],
                      "mismatches": {k: v["host_replay_mismatches"] for k, v in report["runs"].items()}}))


def exposure(args):
    root = args.root.resolve()
    plan_sha = check_plan(root)
    ver = read_json(root / "e009-verification/verification.json")
    if not ver["admitted"]:
        raise SystemExit("host-replay-not-admitted")
    runner = load_runner()
    inputs = read_json(INPUT_EVIDENCE)
    sem_path = root / "semantic/semantic.json"
    rankings = read_json(sem_path)["rankings"]
    support = read_json(root / "support.json")
    nodes = {p.stem for p in (root / "retrieval-input/corpus").glob("*.md")}
    out_dir = root / "exposure"
    out_dir.mkdir()
    summary = []
    for rec in inputs["versions"]:
        vid = rec["version_id"]
        for tag, fname, key in (("main", "graph_edges.csv", "edge_file_sha256"),
                                ("sensitivity", "graph_edges_sensitivity.csv", "sensitivity_edge_file_sha256")):
            path = root / "versions" / vid / fname
            if sha256_file(path) != rec[key]:
                raise SystemExit(f"edge-file-hash-mismatch:{vid}:{tag}")
            emap = edge_map(path, nodes, mode="outgoing")
            res = replay(runner, rankings, emap, "outgoing")
            per_q = []
            for qid in sorted(res):
                r, sup = res[qid], support[qid]
                seeds, fb = set(r["seeds"]), set(r["fallback"])
                expanded_sel = [d for d in r["selected"] if d not in seeds and d not in fb]
                per_q.append({"question_id": qid, "seed": r["seeds"][0], "selected": r["selected"],
                              "fallback": r["fallback"], "candidate_pool": len(r["candidates"]),
                              "expanded_reachable": len(r["expanded"]),
                              "seed_outdegree": len(emap.get(r["seeds"][0], ())),
                              "support_in_seeds": any(d in seeds for d in sup),
                              "support_in_final": any(d in r["selected"] for d in sup),
                              "expansion_added": any(d in expanded_sel for d in sup),
                              "nonseed_added": any(d in r["selected"] and d not in seeds for d in sup),
                              "top1_expansion_added": sup[0] in expanded_sel})
            (out_dir / f"{vid}-{tag}.jsonl").write_text(
                "".join(json.dumps(x, sort_keys=True) + "\n" for x in per_q), encoding="utf-8", newline="\n")
            n = len(per_q)
            mean = lambda k: sum(float(x[k]) for x in per_q) / n
            summary.append({"version_id": vid, "edge_set": tag, "questions": n,
                            "expansion_added_rate": mean("expansion_added"), "nonseed_added_rate": mean("nonseed_added"),
                            "support_in_final_rate": mean("support_in_final"), "support_in_seeds_rate": mean("support_in_seeds"),
                            "top1_expansion_added_rate": mean("top1_expansion_added"),
                            "mean_candidate_pool": mean("candidate_pool"), "mean_reachable": mean("expanded_reachable"),
                            "mean_seed_outdegree": mean("seed_outdegree"),
                            "fallback_question_rate": sum(bool(x["fallback"]) for x in per_q) / n,
                            "mean_fallback_items": sum(len(x["fallback"]) for x in per_q) / n,
                            "per_question_sha256": sha256_file(out_dir / f"{vid}-{tag}.jsonl")})
        print(vid, summary[-2]["expansion_added_rate"], flush=True)
    result = {"schema_version": SCHEMA + ".exposure", "analysis_plan_sha256": plan_sha,
              "semantic_sha256": sha256_file(sem_path), "verification_sha256": sha256_file(root / "e009-verification/verification.json"),
              "support_label": "automatic candidate support (historical lexical top_docs), not qrels",
              "summary": summary}
    print(json.dumps({"exposure_sha256": write_new(out_dir / "exposure.json", result)}))


def ranks(values):
    import numpy as np
    from scipy.stats import rankdata
    return rankdata(np.asarray(values, dtype=float))


def spearman(x, y):
    import numpy as np
    rx, ry = ranks(x), ranks(y)
    if np.std(rx) == 0 or np.std(ry) == 0:
        return None
    return float(np.corrcoef(rx, ry)[0, 1])


def perm_spearman(x, y, seed, n=20000):
    import numpy as np
    rho = spearman(x, y)
    if rho is None:
        return {"rho": None, "p": None, "status": "unavailable_zero_variance"}
    rng = np.random.default_rng(seed)
    rx, ry = ranks(x), ranks(y)
    rx = (rx - rx.mean()) / rx.std()
    hits = 0
    for _ in range(n):
        p = rng.permutation(ry)
        r = float(np.corrcoef(rx, p)[0, 1])
        hits += abs(r) >= abs(rho) - 1e-12
    return {"rho": rho, "p": (1 + hits) / (1 + n), "permutations": n, "status": "computed"}


def boot_spearman(x, y, seed, n=2000):
    import numpy as np
    rng = np.random.default_rng(seed)
    x, y = np.asarray(x, float), np.asarray(y, float)
    vals = []
    for _ in range(n):
        i = rng.integers(0, len(x), len(x))
        r = spearman(x[i], y[i])
        if r is not None:
            vals.append(r)
    if not vals:
        return None
    return [float(np.percentile(vals, 2.5)), float(np.percentile(vals, 97.5)), len(vals)]


def residualize(target, controls):
    import numpy as np
    X = np.column_stack([np.ones(len(target))] + [ranks(c) for c in controls])
    beta = np.linalg.pinv(X) @ target
    return target - X @ beta, X


def partial_spearman(x, y, controls, seed, n=20000):
    import numpy as np
    rx, ry = ranks(x), ranks(y)
    ex, X = residualize(rx, controls)
    ey, _ = residualize(ry, controls)
    if np.std(ex) < 1e-12 or np.std(ey) < 1e-12:
        return {"rho_partial": None, "p": None, "status": "unavailable_zero_residual_variance",
                "control_rank": int(np.linalg.matrix_rank(X))}
    rho = float(np.corrcoef(ex, ey)[0, 1])
    fitted_y = ry - ey
    rng = np.random.default_rng(seed)
    hits = 0
    pinv = np.linalg.pinv(X)
    for _ in range(n):
        ystar = fitted_y + rng.permutation(ey)
        e2 = ystar - X @ (pinv @ ystar)
        r = float(np.corrcoef(ex, e2)[0, 1])
        hits += abs(r) >= abs(rho) - 1e-12
    return {"rho_partial": rho, "p": (1 + hits) / (1 + n), "permutations": n, "status": "computed",
            "control_rank": int(np.linalg.matrix_rank(X))}


def loo_r2(y, predictors):
    import numpy as np
    ry = ranks(y)
    X = np.column_stack([np.ones(len(ry))] + [ranks(p) for p in predictors])
    preds = []
    for i in range(len(ry)):
        m = np.ones(len(ry), bool)
        m[i] = False
        beta = np.linalg.pinv(X[m]) @ ry[m]
        preds.append(float(X[i] @ beta))
    preds = np.asarray(preds)
    return float(1 - np.sum((ry - preds) ** 2) / np.sum((ry - ry.mean()) ** 2))


def holm(pvalues):
    items = sorted((p, k) for k, p in pvalues.items() if p is not None)
    out, running = {}, 0.0
    m = len(items)
    for i, (p, k) in enumerate(items):
        running = max(running, min(1.0, (m - i) * p))
        out[k] = running
    for k, p in pvalues.items():
        if p is None:
            out[k] = None
    return out


def mcnemar(a, b):
    from scipy.stats import binomtest
    b01 = sum(1 for x, y in zip(a, b) if x and not y)
    b10 = sum(1 for x, y in zip(a, b) if y and not x)
    p = binomtest(b01, b01 + b10, 0.5).pvalue if b01 + b10 else 1.0
    return {"first_only": b01, "second_only": b10, "exact_p": float(p)}


def boot_diff(a, b, seed, n=5000):
    import numpy as np
    a, b = np.asarray(a, float), np.asarray(b, float)
    rng = np.random.default_rng(seed)
    d = [float((a[i] - b[i]).mean()) for i in (rng.integers(0, len(a), len(a)) for _ in range(n))]
    return [float(np.percentile(d, 2.5)), float(np.percentile(d, 97.5))]


PRIMARY = ("lambda1_q0.25", "lambda2_q0.25")
CONTROLS = ("outdegree_gini", "sources", "sinks", "mean_seed_outdegree")


def family_table(meas, expo, tag):
    rows = {}
    for row in meas["versions"]:
        m = row[tag]
        lw, rt = m["lwcc"], m["retrieval"]
        rec = {"version_id": row["version_id"], "family": row["family"]}
        for q in Q_VALUES:
            rec[f"lambda1_q{q:.2f}"] = lw["spectra"][f"{q:.2f}"]["lambda1"]
            rec[f"lambda2_q{q:.2f}"] = lw["spectra"][f"{q:.2f}"]["lambda2"]
        rec["subspace_polarization_v1"] = lw["spectra"]["subspace_polarization_v1"]["value"]
        rec["subspace_status"] = lw["spectra"]["subspace_polarization_v1"]["status"]
        rec["retrieval_lambda1_q0.25"] = rt["spectra"]["0.25"]["lambda1"]
        rec["retrieval_lambda2_q0.25"] = rt["spectra"]["0.25"]["lambda2"]
        rec["undirected_lambda2_q0"] = lw["spectra"]["0.00"]["lambda2"]
        rec["normalized_undirected_lambda2"] = lw["normalized_undirected_lambda2"]
        rec["lwcc_total_degree_sha256"] = lw["controls"]["total_degree_sha256"]
        rec["retrieval_total_degree_sha256"] = rt["controls"]["total_degree_sha256"]
        for k in ("outdegree_gini", "sources", "sinks", "isolated", "zero_outdegree"):
            rec[k] = rt["controls"][k]
            rec["lwcc_" + k] = lw["controls"][k]
        rows[row["version_id"]] = rec
    for s in expo["summary"]:
        if s["edge_set"] == tag:
            rows[s["version_id"]].update({k: v for k, v in s.items() if k not in ("version_id", "edge_set")})
    return rows


def frozen_tests(rows, seed_base):
    test = [r for r in rows.values() if r["family"] in ("random_half",) or r["family"].startswith("partial_")]
    rnd = [r for r in test if r["family"] == "random_half"]
    par = [r for r in test if r["family"].startswith("partial_")]
    y = [r["expansion_added_rate"] for r in test]
    out = {"n_versions": len(test), "primary": {}, "incremental": {}, "robustness": {}, "secondary_descriptive": {}}
    for k in PRIMARY:
        x = [r[k] for r in test]
        res = perm_spearman(x, y, 90999 + seed_base)
        res["bootstrap95"] = boot_spearman(x, y, 90998 + seed_base) if res["rho"] is not None else None
        out["primary"][k] = res
    adj = holm({k: v["p"] for k, v in out["primary"].items()})
    for k in PRIMARY:
        out["primary"][k]["holm_p"] = adj[k]
    controls = [[r[c] for r in test] for c in CONTROLS]
    stage2 = {}
    for k in PRIMARY:
        x = [r[k] for r in test]
        inc = {"joint": partial_spearman(x, y, controls, 90996 + seed_base)}
        inc["single"] = {c: partial_spearman(x, y, [ctrl], 90996 + seed_base, n=5000)
                         for c, ctrl in zip(CONTROLS, controls)}
        inc["mechanistic_reference"] = partial_spearman(x, y, [[r["mean_reachable"] for r in test]], 90996 + seed_base, n=5000)
        inc["loo_r2_controls"] = loo_r2(y, controls)
        inc["loo_r2_controls_plus_measure"] = loo_r2(y, controls + [x])
        out["incremental"][k] = inc
        p = out["primary"][k]
        if p["holm_p"] is not None and p["holm_p"] < 0.05 and inc["joint"]["rho_partial"] is not None \
                and (inc["joint"]["rho_partial"] > 0) == (p["rho"] > 0):
            stage2[k] = inc["joint"]["p"]
    adj2 = holm(stage2) if stage2 else {}
    for k in PRIMARY:
        out["incremental"][k]["reached_stage"] = k in stage2
        out["incremental"][k]["holm_p"] = adj2.get(k)
        out["incremental"][k]["adds_information"] = bool(k in adj2 and adj2[k] is not None and adj2[k] < 0.05)
    for k in PRIMARY:
        a = perm_spearman([r[k] for r in rnd], [r["expansion_added_rate"] for r in rnd], 90995 + seed_base)
        b = spearman([r[k] for r in par], [r["expansion_added_rate"] for r in par])
        out["robustness"][k] = {"random20": a, "partial9_rho": b,
                                "sign_agrees": bool(a["rho"] and b and (a["rho"] > 0) == (b > 0))}
    sec = [f"lambda{i}_q{q:.2f}" for q in Q_VALUES for i in (1, 2)] + ["subspace_polarization_v1",
           "retrieval_lambda1_q0.25", "retrieval_lambda2_q0.25"] + list(CONTROLS) + ["mean_reachable"]
    for k in sec:
        xs = [r[k] for r in test]
        out["secondary_descriptive"][k] = None if any(v is None for v in xs) else spearman(xs, y)
    return out


def analyze(args):
    import numpy as np
    root = args.root.resolve()
    plan_sha = check_plan(root)
    meas = read_json(root / "measures/measures.json")
    expo = read_json(root / "exposure/exposure.json")
    if meas["analysis_plan_sha256"] != plan_sha or expo["analysis_plan_sha256"] != plan_sha:
        raise SystemExit("plan-hash-mismatch")
    result = {"schema_version": SCHEMA + ".analysis", "analysis_plan_sha256": plan_sha,
              "measures_sha256": sha256_file(root / "measures/measures.json"),
              "exposure_sha256": sha256_file(root / "exposure/exposure.json"),
              "support_label": "automatic candidate support, not qrels", "families": {}}
    for i, tag in enumerate(("main", "sensitivity")):
        rows = family_table(meas, expo, tag)
        fam = {"table": list(rows.values()), "tests": frozen_tests(rows, i * 100)}
        # sanity checks
        vals = list(rows.values())
        F, R = rows["F"], rows["R"]
        fam["sanity"] = {
            "undirected_lambda2_q0_range": float(np.ptp([r["undirected_lambda2_q0"] for r in vals if r["version_id"] != "U"])),
            "normalized_undirected_lambda2_range": float(np.ptp([r["normalized_undirected_lambda2"] for r in vals])),
            "total_degree_identical_lwcc": len({r["lwcc_total_degree_sha256"] for r in vals if r["version_id"] != "U"}) == 1,
            "total_degree_identical_retrieval": len({r["retrieval_total_degree_sha256"] for r in vals if r["version_id"] != "U"}) == 1,
            "F_R_max_abs_lambda_difference": max(abs(F[k] - R[k]) for k in F if k.startswith("lambda")),
            "seed_rate_range": float(np.ptp([r["support_in_seeds_rate"] for r in vals])),
        }
        per_q = {v: read_jsonl(root / "exposure" / f"{v}-{tag}.jsonl") for v in ("F", "R", "U")}
        ind = {v: [x["expansion_added"] for x in per_q[v]] for v in per_q}
        fam["contrasts"] = {"rates": {v: sum(ind[v]) / len(ind[v]) for v in ind}}
        for a, b, s in (("F", "R", 1), ("F", "U", 2), ("R", "U", 3)):
            fam["contrasts"][f"{a}-{b}"] = dict(mcnemar(ind[a], ind[b]),
                                                difference=sum(ind[a]) / len(ind[a]) - sum(ind[b]) / len(ind[b]),
                                                bootstrap95=boot_diff(ind[a], ind[b], 90997 + s + i * 100))
        result["families"][tag] = fam
    print(json.dumps({"analysis_sha256": write_new(root / "analysis/analysis.json", result)}))


ANSWER_SYSTEM = (
    "You answer questions about Docker using only the numbered context passages supplied with the question. "
    "Do not use outside knowledge. Cite every factual claim with the bracketed context ID it comes from, for example [C2]. "
    "If the passages do not contain enough information to answer, say what is missing instead of guessing. "
    "Keep the answer under 200 words.")
ANSWER_MODEL = {"provider": "openai_responses", "model": "gpt-6-luna", "reasoning_effort": "medium",
                "max_output_tokens": 2048, "store": False, "service_tier": "default", "automatic_retries": 0,
                "price_usd_per_million": {"input": 0.10, "cached_input": 0.01, "cache_write": 0.125, "output": 0.50},
                "price_source": "git/context/scripts/agent_model_provider.py CostLedger (Sprint 07 v5 canary)"}
CONDITIONS = ("F", "R", "U")


def answer_messages(question: str, context: list[dict]) -> list[dict]:
    blocks = "\n\n".join(f"[C{i}] (document {c['doc_id']})\n{c['text']}" for i, c in enumerate(context, start=1))
    return [{"role": "system", "content": ANSWER_SYSTEM},
            {"role": "user", "content": f"Question: {question}\n\nContext passages:\n\n{blocks}"}]


def prepare_answers(args):
    """Step 7 preparation only: frozen requests and a cost manifest. No model call."""
    root = args.root.resolve()
    plan_sha = check_plan(root)
    runner = load_runner()
    inputs = read_json(INPUT_EVIDENCE)
    qids = inputs["answer_set"]["question_ids"]
    questions = {r["question_id"]: r["question"] for r in read_jsonl(root / "retrieval-input/questions.jsonl")}
    corpus_dir = root / "retrieval-input/corpus"
    per = {v: {r["question_id"]: r for r in read_jsonl(root / "exposure" / f"{v}-main.jsonl")} for v in CONDITIONS}
    out = root / "answer-set"
    out.mkdir()
    requests, chars = [], []
    for qid in qids:
        for cond in CONDITIONS:
            ids = per[cond][qid]["selected"]
            ctx = [{"doc_id": d, "text": runner.answer_text((corpus_dir / f"{d}.md").read_text(encoding="utf-8"),
                                                             max_chars=E009["max_context_chars_per_doc"])} for d in ids]
            msgs = answer_messages(questions[qid], ctx)
            body = canon(msgs)
            chars.append(len(body.decode()))
            requests.append({"request_id": sha256_bytes((qid + ":" + cond).encode())[:12], "question_id": qid,
                             "condition": cond, "context_ids": ids, "messages": msgs, "messages_sha256": sha256_bytes(body)})
    (out / "requests.jsonl").write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in requests),
                                        encoding="utf-8", newline="\n")
    identical = sum(1 for q in qids if per["F"][q]["selected"] == per["R"][q]["selected"] == per["U"][q]["selected"])
    # Conservative token bound: 1 token per 3 characters plus overhead; no tokenizer is called here.
    max_in = max(chars) // 3 + 200
    price = ANSWER_MODEL["price_usd_per_million"]
    worst = len(requests) * (max_in * price["cache_write"] + ANSWER_MODEL["max_output_tokens"] * price["output"]) / 1e6
    typical = len(requests) * ((sum(chars) / len(chars)) / 4 * price["input"] + 600 * price["output"]) / 1e6
    manifest = {"schema_version": SCHEMA + ".answer_manifest", "status": "prepared_not_run_awaiting_spend_authorization",
                "analysis_plan_sha256": plan_sha, "input_evidence_sha256": sha256_file(INPUT_EVIDENCE),
                "exposure_sha256": sha256_file(root / "exposure/exposure.json"),
                "selection_rule": inputs["answer_set"]["rule"], "question_ids": qids,
                "conditions": {"F": "as-written links", "R": "every one-way link reversed", "U": "two-way links"},
                "retrieval": "E009 outgoing over the condition's edge file with the shared MiniLM seed; contexts are the exposure-stage final contexts",
                "answer_model": ANSWER_MODEL, "system_prompt": ANSWER_SYSTEM,
                "system_prompt_sha256": sha256_bytes(ANSWER_SYSTEM.encode()),
                "requests": len(requests), "requests_sha256": sha256_file(out / "requests.jsonl"),
                "questions_with_identical_contexts_across_FRU": identical,
                "outbound_data": "question text and the four context excerpts per request (Docker documentation, public source) to the OpenAI Responses API; no references, rubrics or support labels",
                "cost": {"max_request_chars": max(chars), "mean_request_chars": sum(chars) / len(chars),
                         "conservative_max_input_tokens": max_in,
                         "worst_case_usd": worst, "typical_estimate_usd": typical,
                         "proposed_spend_stop_usd": 0.25},
                "grading_packet": "after authorization: randomized per-question labels A/B/C, exact cited context excerpts, rubric (core sufficiency, correctness, unsupported claims, citation support), key stored separately; new packet distinct from HR-01"}
    print(json.dumps({"answer_manifest_sha256": write_new(out / "answer-manifest.json", manifest),
                      "requests": len(requests), "identical_FRU": identical, "worst_case_usd": round(worst, 4)}))


RESULTS_JSON = CONTEXT / "evidence/orientation-spectra-results-v1-2026-10-06.json"
RESULTS_MD = CONTEXT / "evidence/orientation-spectra-results-v1-2026-10-06.md"


def summarize(args):
    """Compact tracked evidence: hashes of every raw output plus the frozen results."""
    root = args.root.resolve()
    plan_sha = check_plan(root)
    analysis = read_json(root / "analysis/analysis.json")
    files = sorted(p for p in root.rglob("*") if p.is_file() and "container-inputs" not in p.parts
                   and "retrieval-input" not in p.parts)
    inventory = {str(p.relative_to(root)).replace("\\", "/"): sha256_file(p) for p in files}
    (root / "output-inventory.json").write_text(json.dumps(inventory, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    keep = ("version_id", "family", "lambda1_q0.25", "lambda2_q0.25", "subspace_polarization_v1", "outdegree_gini",
            "sources", "sinks", "mean_seed_outdegree", "mean_reachable", "mean_candidate_pool",
            "expansion_added_rate", "nonseed_added_rate", "support_in_final_rate", "top1_expansion_added_rate",
            "fallback_question_rate", "mean_fallback_items")
    compact = {"schema_version": SCHEMA + ".results", "analysis_plan_sha256": plan_sha,
               "input_evidence_sha256": sha256_file(INPUT_EVIDENCE),
               "analysis_sha256": sha256_file(root / "analysis/analysis.json"),
               "verification_sha256": sha256_file(root / "e009-verification/verification.json"),
               "answer_manifest_sha256": sha256_file(root / "answer-set/answer-manifest.json"),
               "script_sha256": {p.name: sha256_file(p) for p in (Path(__file__), SEMANTIC_SCRIPT)},
               "raw_inventory_sha256": sha256_file(root / "output-inventory.json"), "raw_files": len(inventory),
               "support_label": "automatic candidate support, not qrels", "families": {}}
    md = ["# Sprint 09c results: directed spectra under controlled link re-orientation", "",
          "Generated by `orientation_spectra_09c.py summarize`. Exposure is measured against **automatic candidate support** "
          "(historical lexical top-5 pages), not qrels. Spectral measures are on `retrieval_no_faq_lwcc`; controls and "
          "exposure use the full retrieval edge set E009 traverses.", ""]
    for tag, fam in analysis["families"].items():
        table = [{k: r.get(k) for k in keep} for r in fam["table"]]
        compact["families"][tag] = {"table": table, "tests": fam["tests"], "sanity": fam["sanity"], "contrasts": fam["contrasts"]}
        md += [f"## {'Main edge set' if tag == 'main' else 'Sensitivity: _index and release-notes edges removed'}", "",
               "| Version | λ₁ q=.25 | λ₂ q=.25 | Subspace v1 | Out Gini | Sources | Sinks | Seed out-deg | Pool | Expansion-added | Non-seed | In final | Fallback Q |",
               "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"]
        for r in table:
            sp = "n/a" if r["subspace_polarization_v1"] is None else f"{r['subspace_polarization_v1']:.4f}"
            md.append(f"| {r['version_id']} | {r['lambda1_q0.25']:.6f} | {r['lambda2_q0.25']:.6f} | {sp} | "
                      f"{r['outdegree_gini']:.4f} | {r['sources']} | {r['sinks']} | {r['mean_seed_outdegree']:.2f} | "
                      f"{r['mean_candidate_pool']:.2f} | {r['expansion_added_rate']:.3f} | {r['nonseed_added_rate']:.3f} | "
                      f"{r['support_in_final_rate']:.3f} | {r['fallback_question_rate']:.3f} |")
        t = fam["tests"]
        md += ["", "Frozen tests over the 29 random and partial versions:", "",
               "| Measure | Spearman ρ | Boot 95% | Perm p | Holm p | Joint partial ρ | Partial p | Adds information |",
               "| --- | ---: | --- | ---: | ---: | ---: | ---: | --- |"]
        for k in PRIMARY:
            p, inc = t["primary"][k], t["incremental"][k]
            b = p["bootstrap95"]
            md.append(f"| {k} | {p['rho']:.3f} | [{b[0]:.3f}, {b[1]:.3f}] | {p['p']:.4f} | {p['holm_p']:.4f} | "
                      f"{inc['joint']['rho_partial']:.3f} | {inc['joint']['p']:.4f} | {'yes' if inc['adds_information'] else 'no'} |")
        md += ["", "Robustness (random 20 versus partial 9): " + "; ".join(
            f"{k} ρ_random={t['robustness'][k]['random20']['rho']:.3f} (p={t['robustness'][k]['random20']['p']:.3f}), "
            f"ρ_partial={t['robustness'][k]['partial9_rho']:.3f}, sign agrees={t['robustness'][k]['sign_agrees']}" for k in PRIMARY), "",
            "F/R/U contrast (expansion-added rate; exact McNemar): " + f"F={fam['contrasts']['rates']['F']:.3f}, "
            f"R={fam['contrasts']['rates']['R']:.3f}, U={fam['contrasts']['rates']['U']:.3f}; " + "; ".join(
            f"{c} diff={fam['contrasts'][c]['difference']:+.3f} [{fam['contrasts'][c]['bootstrap95'][0]:+.3f}, "
            f"{fam['contrasts'][c]['bootstrap95'][1]:+.3f}], p={fam['contrasts'][c]['exact_p']:.4f}" for c in ("F-R", "F-U", "R-U")), "",
            "Secondary/descriptive Spearman with expansion-added rate (exploratory, untested): " + ", ".join(
            f"{k}={v:.3f}" for k, v in t["secondary_descriptive"].items() if v is not None), ""]
    print(json.dumps({"results_sha256": write_new(RESULTS_JSON, compact)}))
    RESULTS_MD.write_text("\n".join(md) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps({"results_md_sha256": sha256_file(RESULTS_MD), "raw_files": len(inventory)}))


ANSWER_MANIFEST_SHA256 = "ca5be8fabc407b38addd2ec1c7a83d600fa4824fb6afe49e502de06080ea43d7"
SPEND_STOP_USD = 0.25
INPUT_CAP_TOKENS = 2500
SDK_DIR = ROOT / ".tools/openai-sdk"


def generate_answers(args):
    """Step 7 after the author's bounded spend authorization (Oct 7, stop $0.25)."""
    import re
    root = args.root.resolve()
    check_plan(root)
    adir = root / "answer-set"
    man_path = adir / "answer-manifest.json"
    if sha256_file(man_path) != ANSWER_MANIFEST_SHA256:
        raise SystemExit("answer-manifest-changed")
    manifest = read_json(man_path)
    if sha256_file(adir / "requests.jsonl") != manifest["requests_sha256"]:
        raise SystemExit("requests-changed")
    if not os.environ.get("OPENAI_API_KEY"):
        raise SystemExit("OPENAI_API_KEY-unavailable")
    sys.path.insert(0, str(SDK_DIR))
    import importlib.metadata
    from openai import OpenAI
    if importlib.metadata.version("openai") != "3.20.0":
        raise SystemExit("openai-sdk-version-mismatch")
    sys.path.insert(0, str(CONTEXT / "scripts"))
    from agent_model_provider import CostLedger
    out = adir / "answers"
    out.mkdir()
    ledger = CostLedger(out, limit_usd=SPEND_STOP_USD)
    ledger.record({"event": "authorization", "author_message": "Spend approved", "stop_usd": SPEND_STOP_USD,
                   "manifest_sha256": ANSWER_MANIFEST_SHA256})
    model = manifest["answer_model"]
    client = OpenAI(max_retries=0, timeout=300)
    requests = read_jsonl(adir / "requests.jsonl")
    results = []
    for i, req in enumerate(requests):
        if sha256_bytes(canon(req["messages"])) != req["messages_sha256"]:
            raise SystemExit("request-hash-mismatch")
        ledger.check_remaining(INPUT_CAP_TOKENS, model["max_output_tokens"], len(requests) - i)
        projected = ledger.admit(INPUT_CAP_TOKENS, model["max_output_tokens"])
        payload = {"model": model["model"], "input": req["messages"], "reasoning": {"effort": model["reasoning_effort"]},
                   "max_output_tokens": model["max_output_tokens"], "store": False, "service_tier": model["service_tier"]}
        t0 = time.time()
        try:
            resp = client.responses.create(**payload)
        except Exception as exc:
            ledger.record({"event": "failed_possible_billable", "projected_usd": projected, "request_id": req["request_id"],
                           "error_type": type(exc).__name__})
            raise
        raw = resp.model_dump(mode="json")
        raw_path = out / f"raw-{req['request_id']}.json"
        raw_path.write_text(json.dumps(raw, sort_keys=True), encoding="utf-8")
        usage = raw.get("usage") or {}
        text = resp.output_text or ""
        if usage.get("input_tokens", 0) > INPUT_CAP_TOKENS:
            ledger.record({"event": "input_cap_exceeded", "request_id": req["request_id"], "usage": usage})
            raise SystemExit("input-cap-exceeded")
        ledger.record({"event": "reported_usage", "request_id": req["request_id"], "usage": usage,
                       "service_tier": raw.get("service_tier"), "response_sha256": sha256_file(raw_path)})
        cited = sorted({int(n) for n in re.findall(r"\[C(\d+)\]", text) if 1 <= int(n) <= len(req["context_ids"])})
        results.append({"request_id": req["request_id"], "question_id": req["question_id"], "condition": req["condition"],
                        "answer": text, "status": raw.get("status"), "incomplete_details": raw.get("incomplete_details"),
                        "model_returned": raw.get("model"), "usage": usage, "seconds": time.time() - t0,
                        "cited_context_numbers": cited, "context_ids": req["context_ids"],
                        "raw_sha256": sha256_file(raw_path)})
        print(i + 1, req["question_id"], req["condition"], raw.get("status"), usage.get("input_tokens"),
              usage.get("output_tokens"), flush=True)
    (out / "answers.jsonl").write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in results),
                                       encoding="utf-8", newline="\n")
    tin = sum(r["usage"].get("input_tokens", 0) for r in results)
    tout = sum(r["usage"].get("output_tokens", 0) for r in results)
    est = tin * 0.10 / 1e6 + tout * 0.50 / 1e6
    receipt = {"schema_version": SCHEMA + ".answer_receipt", "manifest_sha256": ANSWER_MANIFEST_SHA256,
               "answers": len(results), "answers_sha256": sha256_file(out / "answers.jsonl"),
               "reported_input_tokens": tin, "reported_output_tokens": tout,
               "estimated_usage_usd": est, "reserved_usd": ledger.reserved,
               "ledger_sha256": sha256_file(ledger.path), "models_returned": sorted({r["model_returned"] for r in results}),
               "statuses": dict(Counter(r["status"] for r in results)), "sdk": "openai==3.20.0", "retries": 0}
    print(json.dumps({"receipt_sha256": write_new(out / "answer-receipt.json", receipt), "estimated_usd": round(est, 5),
                      "statuses": receipt["statuses"]}))


PACKET_SALT = "icsc2027-09c-blind-labels-v1:"
VALIDATIONS = ROOT / "validations"


def build_packet(args):
    import html
    import random
    root = args.root.resolve()
    adir = root / "answer-set"
    receipt = read_json(adir / "answers/answer-receipt.json")
    if sha256_file(adir / "answers/answers.jsonl") != receipt["answers_sha256"]:
        raise SystemExit("answers-changed")
    runner = load_runner()
    answers = read_jsonl(adir / "answers/answers.jsonl")
    questions = {r["question_id"]: r["question"] for r in read_jsonl(root / "retrieval-input/questions.jsonl")}
    corpus = root / "retrieval-input/corpus"
    by_q = defaultdict(dict)
    for a in answers:
        by_q[a["question_id"]][a["condition"]] = a
    qids = read_json(adir / "answer-manifest.json")["question_ids"]
    key, md, page = {}, [], []
    md += ["# Sprint 09c blind answer grading form", "",
           "Grade each answer only against its own cited excerpts and your reading of the question. Labels are randomized per question.",
           "Rubric per answer: **Core sufficiency** (sufficient / partial / insufficient); **Correctness** (correct / minor error / incorrect); "
           "**Unsupported claims** (none / some / many); **Citation support** (supported / partial / unsupported / no citations).", ""]
    for n, qid in enumerate(qids, start=1):
        labels = list(CONDITIONS)
        random.Random(sha256_bytes((PACKET_SALT + qid).encode())).shuffle(labels)
        key[qid] = {lab: cond for lab, cond in zip("ABC", labels)}
        md += [f"## Q{n:02d}. {questions[qid]}", ""]
        page += [f"<section><h2>Q{n:02d}. {html.escape(questions[qid])}</h2>"]
        for lab, cond in zip("ABC", labels):
            a = by_q[qid][cond]
            md += [f"### Answer {lab}", "", "Core sufficiency: ___  Correctness: ___  Unsupported claims: ___  Citation support: ___  Notes: ___", ""]
            page += [f"<article><h3>Answer {lab}</h3><div class='ans'>{html.escape(a['answer'] or '(empty response; status ' + str(a['status']) + ')')}</div>"]
            if not a["cited_context_numbers"]:
                page += ["<p class='none'>No context citations.</p>"]
            for c in a["cited_context_numbers"]:
                doc = a["context_ids"][c - 1]
                text = runner.answer_text((corpus / f"{doc}.md").read_text(encoding="utf-8"), max_chars=E009["max_context_chars_per_doc"])
                page += [f"<details><summary>[C{c}] {html.escape(doc)}</summary><pre>{html.escape(text)}</pre></details>"]
            page += ["</article>"]
        page += ["</section>"]
    style = ("body{font-family:system-ui,sans-serif;max-width:900px;margin:0 auto;padding:16px;background:#fff;color:#111}"
             "article{border:1px solid #ccc;border-radius:6px;padding:12px;margin:12px 0}.ans{white-space:pre-wrap}"
             "pre{white-space:pre-wrap;background:#f4f4f4;padding:8px}.none{color:#a33}"
             "@media (prefers-color-scheme:dark){body{background:#151515;color:#eee}pre{background:#222}article{border-color:#444}}")
    doc = ("<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
           f"<title>09c Blind Answers</title><style>{style}</style></head><body><h1>Sprint 09c blind answer packet</h1>"
           "<p>20 questions, three answers each. Labels A/B/C are randomized per question; the key is stored separately. "
           "Each answer's cited context excerpts are exactly what the model saw.</p>" + "".join(page) + "</body></html>")
    html_path = VALIDATIONS / "orientation-spectra-blind-answers-2026-10-07.html"
    form_path = VALIDATIONS / "orientation-spectra-answer-grading-form-2026-10-07.md"
    for p in (html_path, form_path):
        if p.exists():
            raise SystemExit(f"refusing-to-overwrite:{p}")
    html_path.write_text(doc, encoding="utf-8")
    form_path.write_text("\n".join(md) + "\n", encoding="utf-8")
    key_doc = {"schema_version": SCHEMA + ".blind_key", "label_rule": f"random.Random(sha256('{PACKET_SALT}' + qid)).shuffle(['F','R','U']) -> A,B,C",
               "answers_sha256": receipt["answers_sha256"], "packet_sha256": sha256_file(html_path),
               "form_sha256": sha256_file(form_path), "key": key}
    print(json.dumps({"key_sha256": write_new(CONTEXT / "evidence/orientation-spectra-answer-key-2026-10-07.json", key_doc),
                      "packet_sha256": sha256_file(html_path), "form_sha256": sha256_file(form_path)}))


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="stage", required=True)
    p = sub.add_parser("freeze")
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--evidence", type=Path, default=INPUT_EVIDENCE)
    stages = {"freeze": freeze, "measures": measures, "semantic": semantic, "verify-e009": verify_e009,
              "exposure": exposure, "analyze": analyze, "prepare-answers": prepare_answers,
              "summarize": summarize, "generate-answers": generate_answers, "build-packet": build_packet}
    for name in stages:
        if name == "freeze":
            continue
        p = sub.add_parser(name)
        p.add_argument("--root", type=Path, required=True)
    args = parser.parse_args()
    stages[args.stage](args)


if __name__ == "__main__":
    main()
