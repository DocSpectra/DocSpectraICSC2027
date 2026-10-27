"""Sprint 10: run contracts for the Docker restructuring study (R0 vs O).

Infrastructure only. No editor, agent, answering or judge model is called and no
network is used. The editor runtime is bound in Sprint 11; until then a launch
accepts only a scripted in-process editor and `mode: fixture` manifests.

Per arm and replicate the host:
  1. copies a fresh FAQ-excluded baseline (Sprint 08 `copy`) into
     runs/<run_id>/<arm>-rNN/editor/source, the only editor-visible location;
  2. runs the editor through EditorSession (file tools only, no metric tool),
     capturing a private transcript and event log under host/;
  3. stores immutable checkpoints (Sprint 08 `checkpoint`, run namespace);
  4. selects the terminal checkpoint (`last_checkpoint_v1`);
  5. derives the merge/move ID mapping from moved content;
  6. runs C v1 host-side on R0/L terminals (private receipt, never retried);
  7. writes a private spectral-profile receipt for every terminal variant;
  8. writes the preservation-review packet and the support/citation mapping.
A separate `grading-export` stage writes the blind provider datasets with the
variant key kept outside the packet. No questions, answers or grades are read.
"""
from __future__ import annotations

import os

os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")  # 09c measured single-threaded

import argparse
import contextlib
import hashlib
import importlib.util
import inspect
import io
import json
import platform
import re
import secrets
import shutil
import sys
import time
import uuid
from collections import defaultdict
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent))
import docker_corpus_variants as dcv  # noqa: E402

ROOT = dcv.ROOT
CONTEXT = ROOT / "git/context"
RA = ROOT / "git/RetrievalArena"
SCHEMA = "docspectra.icsc.restructuring_run.v1"
UNRESOLVED = "UNRESOLVED"
ARMS = ("O", "S", "R0", "L")
EDITED = ("R0", "L")
STATES = ("completed", "no_op", "failed", "budget_stop", "c_rejected", "preservation_flagged")
TEMPLATES = CONTEXT / "evidence/restructuring-arm-templates-v1-2026-10-07.json"
EDITOR_TOOLS = ("list_files", "read_file", "write_file", "delete_file", "checkpoint", "finish")
LIMIT_KEYS = ("model_calls", "tool_calls", "tokens", "wall_seconds", "edits")
GENERIC_ACK = {"ok": True}
FIXTURE_LIMITS = {"model_calls": 0, "tool_calls": 200, "tokens": 0, "wall_seconds": 600, "edits": 50}
GENERIC_REFUSAL = {"ok": False, "error": "operation refused"}

# C v1 pins (variant manifest v5 `structural_target_pin`).
STRUCT_CODE = RA / "retrieval_arena/graph/structural_target.py"
STRUCT_CODE_SHA256 = "08658f1b61c6cd9d1566a1fc77abdd0b4b5e68d8de36242a48461b17d9562895"
STRUCT_CONFIG_SHA256 = "7aad8fba4b6dbc9fbd684d8306645eb5a219ead151bcb2796b104e31ae531c09"
C_VERSION = "structural_connectivity_constraint.v1"

# Spectral profile pins: the 09c measurement functions, by source hash. The 09c
# script file gained later stages after its measures ran, so the file hash is
# recorded but the gate is on the measuring functions and the F reproduction.
SCRIPT_09C = CONTEXT / "scripts/orientation_spectra_09c.py"
FUNCTIONS_09C = {
    "gini": "9a05fb7d54d85871a602e5c70f19d963b26cf702c024b8f1d4f81406648a30ec",
    "degree_controls": "e1009c2e232d889f2ec094ea6fc3729eb573500211f8be2a52a1e504a6dc5532",
    "normalized_lambda2": "08adf78d214817048276c25f2fa8141f0aaa748912c8d8b9dace5fc70bf9a9bf",
    "spectra": "6d16a5ebddff5c1292982d48d999ed1180e2992e266c9784c738b96be6bef4c3",
    "weak_components": "9214bc1f8c0da4a976bce831ee929accbfd00ab08286c9d081cb69c3ef5b3056",
    "load_phase": "24b808e2e515f8c71c5925ffb142194ac6c767e91fd68d8ecc92efa8e5da327d",
}
PHASE_CODE_SHA256 = "d937edc46d75ac6094e5a8f21ddb0eb3156956adb73232b85aaced7d410a65fe"
PHASE_CONFIG_SHA256 = "b22cebd5d92dd08fec6ea7a0afa31ca9335847b38a20988d08d95d321d1044be"
Q_SWEEP = [0.0, 0.05, 0.10, 0.15, 0.20, 0.25]
SPECTRAL_VERSION = "docspectra.icsc.spectral_profile.v1"
# 09c F (measures.json 90592d0f…), main edge set, retrieval_no_faq_lwcc.
F_REFERENCE = {"graph_sha256": "05405ef00715fb1dd331946b36a51212b726290cc06c680df9d92af2e0774f3c",
               "lambda1_q0.25": 0.08630868621054655, "normalized_lambda2": 0.011037979927050976,
               "lwcc_nodes": 844, "measures_sha256": "90592d0f8a2ffcd8833e836a03b35c58ea4f4fa2864e18383ba83573de8bf1d3"}
# Sprint 10b: the docs scope changes the retrieval-view hash but not the LWCC, so
# the reference applies on the LWCC digest (structural_target.graph_digest).
F_REFERENCE_LWCC = {"lwcc_graph_sha256": "b12e335e8852c54bbcacdd30728689bba45e5337a6d4239b0f1c7796b4a1805c",
                    "lambda1_q0.25": F_REFERENCE["lambda1_q0.25"], "normalized_lambda2": F_REFERENCE["normalized_lambda2"],
                    "lwcc_nodes": 844, "lwcc_edges": 2414, "measures_sha256": F_REFERENCE["measures_sha256"],
                    "applies_on": "retrieval_no_faq_lwcc graph_sha256"}
MAPPING_RULE = {"version": "moved_content_majority_v1", "min_share": 0.5,
                "unit": "normalized non-blank line, link targets stripped, lines without alphanumerics dropped",
                "tie_break": "smallest variant doc id"}
LINK_TEXT = re.compile(r"(?<!!)\[([^\]]*)\]\([^)]*\)")


class BudgetStop(Exception):
    """Raised inside the editor when a declared limit is reached."""


class Refused(ValueError):
    pass


# ---------------------------------------------------------------- small helpers

def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canon(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()


def read_json(path: Path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_new(path: Path, data) -> str:
    dcv.write_new(Path(path), data)
    return dcv.file_hash(Path(path))


def quiet(fn, *args, **kwargs):
    """Sprint 08 stages print receipts; keep them out of the run's stdout."""
    with contextlib.redirect_stdout(io.StringIO()):
        return fn(*args, **kwargs)


def resolve(path) -> Path:
    path = Path(path)
    return (path if path.is_absolute() else ROOT / path).resolve()


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------- pinned code

SANDBOX = CONTEXT / "sandbox"
AUTH_ENV = "DOCSPECTRA_CODEX_AUTH"  # host path to the author's Codex auth.json; never copied to artifacts


def load_sandbox():
    if str(SANDBOX) not in sys.path:
        sys.path.insert(0, str(SANDBOX))
    import editor_session
    return editor_session


def load_struct():
    if dcv.file_hash(STRUCT_CODE) != STRUCT_CODE_SHA256:
        raise Refused("structural-code-identity-mismatch")
    if str(RA) not in sys.path:
        sys.path.insert(0, str(RA))
    from retrieval_arena.graph import structural_target as st
    return st


def load_spectral():
    o9 = load_module(SCRIPT_09C, "o9c_pinned")
    for name, expected in FUNCTIONS_09C.items():
        if sha256_bytes(inspect.getsource(getattr(o9, name)).encode()) != expected:
            raise Refused(f"spectral-code-identity-mismatch:{name}")
    if o9.PHASE_CODE_SHA256 != PHASE_CODE_SHA256 or o9.PHASE_CONFIG_SHA256 != PHASE_CONFIG_SHA256:
        raise Refused("spectral-phase-pin-mismatch")
    if [0.0] + list(o9.Q_VALUES) != Q_SWEEP:
        raise Refused("spectral-q-sweep-mismatch")
    try:
        mp = o9.load_phase()  # refuses on magnetic_phase code or config drift
    except SystemExit as error:
        raise Refused(f"spectral-{error}") from None
    return o9, mp


def spectral_pin(f_reference=None):
    return {"version": SPECTRAL_VERSION, "functions_09c_sha256": FUNCTIONS_09C,
            "script_09c_path": "git/context/scripts/orientation_spectra_09c.py",
            "phase_code_sha256": PHASE_CODE_SHA256, "phase_config_sha256": PHASE_CONFIG_SHA256,
            "q_sweep": Q_SWEEP, "subspace_q": 0.25,
            "views": {"magnetic_and_normalized_lambda2": "retrieval_no_faq_lwcc",
                      "degree_controls": ["retrieval_no_faq", "retrieval_no_faq_lwcc"],
                      "retrieval_magnetic_q": [0.0, 0.25], "T_descriptor": "retrieval_no_faq"},
            "T_descriptor": {"version": "anchored_forward_reachability.v1", "depth": 3, "admitted": False},
            "structural_code_sha256": STRUCT_CODE_SHA256, "structural_config_sha256": STRUCT_CONFIG_SHA256,
            "f_reference": f_reference or F_REFERENCE, "role": "reported descriptor; never editor-visible, never a target or admission rule"}


# ---------------------------------------------------------------- graphs

def spectral_pin_for(vm):
    """v1-v5 manifests (repository_root) keep the Sprint 10 pin; docs scope uses the LWCC reference."""
    return spectral_pin(None if dcv.scope_of(vm) == dcv.DEFAULT_SCOPE else F_REFERENCE_LWCC)


def tree_graph(gm, tree: Path, scope: str = dcv.DEFAULT_SCOPE):
    """Doc-id nodes and author-link edges of an editor tree (retrieval_no_faq); out-of-scope paths refused."""
    files = dcv.inventory(tree)
    try:
        page_list = dcv.pages(gm, tree, scope, refuse=True)
    except ValueError as error:
        raise Refused(str(error)) from None
    if len(page_list) != len(files) or any(p.is_faq for p in page_list):
        raise Refused("tree-nonpage-or-faq")
    ids = [p.doc_id for p in page_list]
    if len(set(ids)) != len(ids):
        raise Refused("document-id-collision")
    return page_list, sorted(ids), sorted(set(gm.extract_markdown_edges(page_list)))


def baseline_pages(gm, variant_manifest):
    source = dcv.source_check(variant_manifest)
    return [p for p in dcv.source_pages(gm, variant_manifest, source) if not p.is_faq]


# ---------------------------------------------------------------- run manifest

REQUIRED = ("schema_version", "mode", "run_id", "created", "variant_manifest", "templates", "baseline",
            "arm_plan", "editor", "allowed", "limits", "stopping", "retry_policy", "output_namespace",
            "cost_admission", "evaluation", "c_pin", "spectral_pin", "mapping_rule", "information_boundary")


def unresolved_paths(value, prefix=""):
    if value == UNRESOLVED:
        return [prefix]
    if isinstance(value, dict):
        return [p for k, v in sorted(value.items()) for p in unresolved_paths(v, f"{prefix}.{k}" if prefix else k)]
    if isinstance(value, list):
        return [p for i, v in enumerate(value) for p in unresolved_paths(v, f"{prefix}[{i}]")]
    return []


def c_pin_from_variant_manifest(vm):
    pin = vm.get("structural_target_pin")
    if not pin or pin.get("decision") != "admit_C_only":
        return None
    rule, code = pin["admission_rule"], pin["code"]
    m = re.search(r"graph_sha256 ([0-9a-f]{64}); lambda2 ([0-9.e-]+); \|M\| (\d+)", rule["baseline"])
    return {"version": rule["version"], "form": rule["form"], "anchors": sorted(rule["anchors"]),
            "code_sha256": code["code_sha256"], "config": code["config"], "config_sha256": code["config_sha256"],
            "ra_commit": code["ra_commit"], "baseline_graph_sha256": m.group(1),
            "baseline_lambda2": float(m.group(2)), "baseline_main_component_size": int(m.group(3)),
            "merge_mapping_required": rule["merge_mapping_required"], "source": "variant_manifest.structural_target_pin"}


def fixture_c_pin(gm, vm, anchors):
    """C pin for a synthetic fixture baseline: same code/config, fixture baseline values."""
    st = load_struct()
    page_list = baseline_pages(gm, vm)
    nodes = sorted(p.doc_id for p in page_list)
    edges = gm.extract_markdown_edges(page_list)
    base = st.baseline_summary(nodes, edges, anchors)
    config = {k: list(v) if isinstance(v, tuple) else v for k, v in st.StructuralConfig().__dict__.items()}
    return {"version": C_VERSION, "form": "C0+C1+C1b+C2", "anchors": sorted(anchors), "code_sha256": STRUCT_CODE_SHA256,
            "config": config, "config_sha256": st.config_digest(), "ra_commit": "eea1e368a8004021da0f2ffafdb6cdf37b6727dc",
            "baseline_graph_sha256": base["graph_sha256"], "baseline_lambda2": base["lambda2"],
            "baseline_main_component_size": len(base["main_component"]), "merge_mapping_required": True,
            "source": "fixture_baseline", "fixture_only": True}


def limits_arg(decided_limits, fixture_limits):
    if decided_limits is None:
        return None
    if fixture_limits:
        raise Refused("decided-and-fixture-limits-both-given")
    if set(decided_limits) != set(LIMIT_KEYS):
        raise Refused("limits-keys")
    return dict(decided_limits)


def build_manifest(*, run_id, variant_manifest_path, mode="fixture", anchors=None, fixture_limits=None, created,
                   templates_path=TEMPLATES, runtime=None, decided_limits=None, stopping=None, model_identity=None):
    """A run manifest. Scientific fields stay UNRESOLVED; fixture limits are marked fixture_only.

    Sprint 11: `runtime` (sandbox/editor_session.runtime_block) binds the container
    runtime; `decided_limits`, `stopping` and `model_identity` carry author decisions only.
    """
    vm_path = resolve(variant_manifest_path)
    vm = read_json(vm_path)
    gm = dcv.pinned(vm)
    c_pin = c_pin_from_variant_manifest(vm)
    if c_pin is None:
        if mode != "fixture" or not anchors:
            raise Refused("variant-manifest-has-no-C-pin")
        c_pin = fixture_c_pin(gm, vm, anchors)
    templates_path = resolve(templates_path)
    templates = read_json(templates_path)
    page_list = baseline_pages(gm, vm)
    edges = gm.extract_markdown_edges(page_list)
    limits = {k: UNRESOLVED for k in LIMIT_KEYS}
    if fixture_limits:
        limits = dict(fixture_limits, fixture_only=True)
    if decided := limits_arg(decided_limits, fixture_limits):
        limits = decided
    manifest = {
        "schema_version": SCHEMA + ".manifest", "mode": mode, "run_id": run_id, "created": created,
        "variant_manifest": {"path": str(vm_path), "sha256": dcv.file_hash(vm_path)},
        "templates": {"path": str(templates_path), "sha256": dcv.file_hash(templates_path)},
        "baseline": {"source_commit": vm["source_commit"], "source_tree_sha256": vm["source_tree_sha256"],
                     "graph_edges_sha256": vm["baseline"]["graph_edges_sha256"],
                     "recomputed_graph_edges_sha256": sha256_bytes(json.dumps(edges, separators=(",", ":")).encode()),
                     "recovered_export": vm.get("recovered_export"),
                     "graph_views": {"admission": "retrieval_no_faq", "spectral": "retrieval_no_faq_lwcc",
                                     "degree_controls": ["retrieval_no_faq", "retrieval_no_faq_lwcc"]}},
        "arm_plan": {"O": {"replicates": 1}, "S": {"replicates": UNRESOLVED},
                     "R0": {"replicates": UNRESOLVED, "note": "r >= 3 suggested; derive from grading budget"},
                     "L": {"replicates": UNRESOLVED, "optional": True}},
        "editor": {"model_identity": UNRESOLVED if mode == "live" else "fixture-scripted-editor",
                   "runtime_identity": UNRESOLVED if mode == "live" else "in-process-fixture",
                   "sandbox_identity": UNRESOLVED,
                   "fresh_start": "new session id per arm and replicate; no inherited history, connectors, web or other variants",
                   "prompts": {arm: templates["arms"][arm].get("prompt") for arm in EDITED}},
        "allowed": {"paths": ["editor/source/** (relative POSIX paths, no '..', no absolute)"],
                    "tools": list(EDITOR_TOOLS), "metric_tool": None,
                    "scope": {"R0": "restructure", "L": "links_only_existing_paths"}},
        "limits": limits,
        "stopping": {"rule": "editor calls finish, or any limit is reached (budget_stop)",
                     "checkpoint_rule": "editor checkpoints after each batch; host checkpoints the tree at finish and at budget stop",
                     "terminal_selection": "last_checkpoint_v1",
                     "budget_stop_terminal_eligible_for_grading": UNRESOLVED},
        "retry_policy": {"editor_failure": "no retry; record failed", "c_rejection": "no retry; record c_rejected",
                         "preservation_flag": "no retry; human review", "namespace_reuse": "refused"},
        "output_namespace": {"root": vm["output_root"], "pattern": "runs/{run_id}/{arm}-r{replicate:02d}",
                             "editor_visible": "editor/source", "private": ["host", "exports"]},
        "cost_admission": {"authorization": UNRESOLVED, "usd_cap": UNRESOLVED},
        "evaluation": {"question_set": UNRESOLVED, "answer_conditions": UNRESOLVED,
                       "grading": "blind; opaque variant ids; key outside packet"},
        "c_pin": c_pin, "spectral_pin": spectral_pin_for(vm), "mapping_rule": MAPPING_RULE,
        "information_boundary": {"editor_sees": ["editor tree", "file-tool results", "generic acknowledgments"],
                                 "never_editor_visible": ["C status or reasons", "spectral profile", "T", "other variants",
                                                          "questions", "answers", "grades", "receipts", "transcripts"]},
    }
    if runtime is not None:  # Sprint 11 container runtime; manifests without it keep the Sprint 10 shape
        manifest["runtime"] = runtime
        manifest["editor"]["runtime_identity"] = f"{runtime['version']}:{runtime['driver']}:codex-cli-{runtime['codex_version']}"
        manifest["editor"]["sandbox_identity"] = {k: runtime[k]["id"] for k in ("editor_image", "proxy_image")}
        if model_identity is not None:
            manifest["editor"]["model_identity"] = model_identity
        manifest["allowed"]["tools"] = ["shell inside the editor container"]
        manifest["information_boundary"]["editor_sees"] = ["editor tree", "its own shell command output"]
        manifest["stopping"]["checkpoint_rule"] = ("host checkpoints the tree at each turn.completed, at finish and "
                                                   "at a stop; edits are net changed paths from host snapshots")
    if stopping:
        manifest["stopping"].update(stopping)
    if dcv.scope_of(vm) != dcv.DEFAULT_SCOPE:  # v1-v5 manifests keep their exact Sprint 10 shape
        manifest["baseline"]["scope"] = {"version": dcv.scope_of(vm),
                                         "excluded_sha256": vm["source_policy"]["excluded"]["sha256"],
                                         "excluded_count": vm["source_policy"]["excluded"]["count"]}
    return manifest


def validate_manifest(manifest, *, live: bool):
    missing = [k for k in REQUIRED if k not in manifest]
    if missing:
        raise Refused("manifest-missing:" + ",".join(missing))
    if manifest["schema_version"] != SCHEMA + ".manifest" or manifest["mode"] not in ("fixture", "live"):
        raise Refused("manifest-schema-or-mode")
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{2,63}", manifest["run_id"]):
        raise Refused("manifest-run-id")
    vm_path = Path(manifest["variant_manifest"]["path"])
    if dcv.file_hash(vm_path) != manifest["variant_manifest"]["sha256"]:
        raise Refused("variant-manifest-hash-drift")
    if dcv.file_hash(Path(manifest["templates"]["path"])) != manifest["templates"]["sha256"]:
        raise Refused("arm-templates-hash-drift")
    vm = read_json(vm_path)
    if vm["source_tree_sha256"] != manifest["baseline"]["source_tree_sha256"]:
        raise Refused("baseline-source-hash-drift")
    if manifest["baseline"]["recomputed_graph_edges_sha256"] != vm["baseline"]["graph_edges_sha256"]:
        raise Refused("baseline-graph-hash-drift")
    expected = c_pin_from_variant_manifest(vm)
    if expected is not None and manifest["c_pin"] != expected:
        raise Refused("c-pin-differs-from-variant-manifest")
    c = manifest["c_pin"]
    if (c["version"], c["code_sha256"], c["config_sha256"]) != (C_VERSION, STRUCT_CODE_SHA256, STRUCT_CONFIG_SHA256):
        raise Refused("c-pin-code-or-config")
    if manifest["spectral_pin"] != spectral_pin_for(vm):
        raise Refused("spectral-pin-drift")
    if manifest["mapping_rule"] != MAPPING_RULE:
        raise Refused("mapping-rule-drift")
    templates = read_json(manifest["templates"]["path"])
    for arm in EDITED:
        prompt = templates["arms"][arm]["prompt"]
        if dcv.file_hash(resolve(prompt["path"])) != prompt["sha256"]:
            raise Refused(f"prompt-bytes-drift:{arm}")
    if "runtime" in manifest:
        drift = load_sandbox().check_runtime_block(manifest["runtime"])
        if drift:
            raise Refused(",".join(drift))
    if live:
        problems = ([] if manifest["mode"] == "live" else ["mode-not-live"]) + unresolved_paths(manifest)
        if "runtime" not in manifest:
            problems.append("runtime-block-missing")
        if c.get("fixture_only") or manifest["limits"].get("fixture_only"):
            problems.append("fixture-only-values")
        if problems:
            raise Refused("live-launch-refused:" + ",".join(problems))
    return vm


# ---------------------------------------------------------------- editor session

class EditorSession:
    """The only surface an editor gets: file tools on its own tree, no metric tool.

    Every return value is editor-visible and is also logged privately. Refusals are
    generic to the editor; the detailed reason goes only to the host event log.
    """

    def __init__(self, tree: Path, limits: dict, scope: str, host, clock=time.monotonic,
                 corpus_scope: str = dcv.DEFAULT_SCOPE):
        self.tree, self.limits, self.scope, self.host, self.clock = tree.resolve(), limits, scope, host, clock
        self.corpus_scope = corpus_scope
        self.calls = self.edits = self.tokens = self.model_calls = 0
        self.started = clock()
        self.visible = []          # every editor-visible response, in order
        self.checkpoints = []      # editor-declared checkpoint tree ids
        self.finished = False
        self.baseline_files = set(dcv.inventory(self.tree))

    def registered_resources(self):
        return [str(self.tree)]

    def _limit(self, key, value):
        cap = self.limits.get(key)
        return isinstance(cap, (int, float)) and value > cap

    def _check(self, name):
        elapsed = self.clock() - self.started
        for key, value in (("model_calls", self.model_calls), ("tool_calls", self.calls), ("edits", self.edits),
                           ("tokens", self.tokens), ("wall_seconds", elapsed)):
            if self._limit(key, value):
                self.host.event("budget_stop", {"limit": key, "value": value, "at": name})
                raise BudgetStop(key)

    def record_model_call(self, tokens):
        """Runtime adapters (Sprint 11) report each model call and its tokens here."""
        if self.finished:
            raise BudgetStop("session-finished")
        self.model_calls += 1
        self.tokens += int(tokens)
        self._check("model_call")
        self.host.transcript({"model_call": self.model_calls, "tokens": int(tokens)})

    def _count(self, name, args, edit=False):
        if self.finished:
            raise BudgetStop("session-finished")
        self.calls += 1
        self.edits += bool(edit)
        self._check(name)
        self.host.transcript({"tool": name, "args": args})

    def _reply(self, value):
        self.visible.append(value)
        self.host.transcript({"visible": value})
        return value

    def _path(self, rel):
        if not isinstance(rel, str) or not rel or rel.startswith(("/", "\\")) or ":" in rel:
            return None
        parts = Path(rel).parts
        if ".." in parts or not rel.endswith(".md"):
            return None
        path = (self.tree / rel).resolve()
        return path if self.tree in path.parents else None

    def _refuse(self, reason):
        self.host.event("tool_refused", {"reason": reason})
        return self._reply(dict(GENERIC_REFUSAL))

    def list_files(self):
        self._count("list_files", {})
        return self._reply(sorted(dcv.inventory(self.tree)))

    def read_file(self, rel):
        self._count("read_file", {"path": rel})
        path = self._path(rel)
        if path is None or not path.is_file():
            return self._refuse("read-outside-tree-or-missing")
        return self._reply(path.read_text(encoding="utf-8"))

    def write_file(self, rel, text):
        self._count("write_file", {"path": rel, "bytes": len(text.encode("utf-8"))}, edit=True)
        path = self._path(rel)
        if path is None:
            return self._refuse("write-outside-tree")
        if dcv.excluded_class(rel, self.corpus_scope):
            return self._refuse("write-out-of-scope-path")
        if self.scope == "links_only_existing_paths" and rel not in self.baseline_files:
            return self._refuse("scope-links-only-new-path")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.encode("utf-8"))
        return self._reply(dict(GENERIC_ACK))

    def delete_file(self, rel):
        self._count("delete_file", {"path": rel}, edit=True)
        path = self._path(rel)
        if path is None or not path.is_file():
            return self._refuse("delete-outside-tree-or-missing")
        if self.scope == "links_only_existing_paths":
            return self._refuse("scope-links-only-delete")
        path.unlink()
        for parent in path.parents:  # keep the tree free of empty directories
            if parent == self.tree or any(parent.iterdir()):
                break
            parent.rmdir()
        return self._reply(dict(GENERIC_ACK))

    def checkpoint(self, reason):
        self._count("checkpoint", {"reason": reason})
        self.checkpoints.append(self.host.checkpoint(self.tree, "candidate", reason))
        return self._reply(dict(GENERIC_ACK))

    def finish(self, summary=""):
        self._count("finish", {"summary": summary})
        self.finished = True
        return self._reply(dict(GENERIC_ACK))


class Host:
    """Private, host-side state of one arm/replicate namespace."""

    def __init__(self, manifest_path: Path, manifest: dict, arm: str, replicate: int, namespace: Path, prompt_sha256):
        self.manifest_path, self.manifest, self.arm, self.replicate = manifest_path, manifest, arm, replicate
        self.ns, self.prompt_sha256 = namespace, prompt_sha256
        self.private = namespace / "host"
        self.private.mkdir(parents=True)
        self.session_id = str(uuid.uuid4())
        self.seq = 0
        self.stored = []
        self.parent = manifest["baseline"]["source_tree_sha256"]

    def _append(self, name, record):
        self.seq += 1
        with (self.private / name).open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(json.dumps(dict(record, seq=self.seq), sort_keys=True, ensure_ascii=False) + "\n")

    def event(self, kind, details=None):
        self._append("events.jsonl", {"kind": kind, "details": details or {}, "time": time.time()})

    def transcript(self, record):
        self._append("transcript.jsonl", record)

    def transcript_sha256(self):
        path = self.private / "transcript.jsonl"
        return dcv.file_hash(path) if path.exists() else None

    def checkpoint(self, tree: Path, status: str, reason: str):
        identity = dcv.tree_hash(dcv.inventory(tree))
        if identity not in self.stored:
            meta = self.private / "checkpoint-metadata" / f"{len(self.stored) + 1:04d}.json"
            write_new(meta, {"arm": self.arm, "parent_tree_sha256": self.parent, "prompt_sha256": self.prompt_sha256,
                             "model_identity": self.manifest["editor"]["model_identity"], "session_id": self.session_id,
                             "tool_transcript_sha256": self.transcript_sha256(), "status": status, "edit_reason": reason})
            quiet(dcv.checkpoint, SimpleNamespace(manifest=Path(self.manifest["variant_manifest"]["path"]),
                                                  candidate=tree, metadata=meta, namespace=self.private))
            self.stored.append(identity)
        self.event("checkpoint", {"content_id": identity, "status": status, "reason": reason})
        self.parent = identity
        return identity

    def checkpoint_source(self, identity):
        return self.private / "checkpoints" / identity / "source"


# ---------------------------------------------------------------- mapping

def content_units(text: str) -> set[str]:
    units = set()
    for line in text.splitlines():
        unit = " ".join(LINK_TEXT.sub(r"\1", line).split())
        if any(ch.isalnum() for ch in unit):
            units.add(unit)
    return units


def derive_mapping(base_pages, variant_pages):
    """moved_content_majority_v1: removed baseline ids -> surviving variant ids."""
    base = {p.doc_id: p for p in base_pages}
    var = {p.doc_id: p for p in variant_pages}
    var_units = {d: content_units(p.text) for d, p in var.items()}
    index = defaultdict(set)
    for d, units in var_units.items():
        for u in units:
            index[u].add(d)
    removed = sorted(set(base) - set(var))
    changed = sorted(d for d in set(base) & set(var) if base[d].text != var[d].text)
    added = sorted(set(var) - set(base))
    mapping, unmapped, translation, partial = {}, [], {}, []
    for d in removed + changed:
        units = content_units(base[d].text)
        holders = defaultdict(int)
        for u in units:
            for v in index.get(u, ()):
                holders[v] += 1
        found = {u for u in units if u in index}
        coverage = len(found) / len(units) if units else None
        ranked = sorted(holders.items(), key=lambda kv: (-kv[1], kv[0]))
        entry = {"rel_path": base[d].rel_path, "kept": d in var, "units": len(units), "coverage": coverage,
                 "holders": [{"doc_id": v, "units": n, "rel_path": var[v].rel_path} for v, n in ranked[:5]]}
        if d in removed:
            share = ranked[0][1] / len(units) if ranked and units else 0.0
            entry["survivor_share"] = share
            if share >= MAPPING_RULE["min_share"]:
                mapping[d] = ranked[0][0]
                entry["survivor"] = ranked[0][0]
            else:
                unmapped.append(d)
                entry["survivor"] = None
        if coverage is not None and coverage < 1:
            partial.append(d)
        translation[d] = entry
    return {"rule": MAPPING_RULE, "mapping": mapping, "unmapped_removals": unmapped, "removed": removed,
            "changed": changed, "added": added, "partial_unit_loss": partial, "translation": translation}


# ---------------------------------------------------------------- C admission

def c_admission(manifest, gm, base_page_list, variant_nodes, variant_edges, mapping):
    st = load_struct()
    pin = manifest["c_pin"]
    config = st.StructuralConfig(**{k: tuple(v) if isinstance(v, list) else v for k, v in pin["config"].items()})
    if st.config_digest(config) != pin["config_sha256"]:
        raise Refused("c-config-identity-mismatch")
    nodes = sorted(p.doc_id for p in base_page_list)
    base = st.baseline_summary(nodes, gm.extract_markdown_edges(base_page_list), pin["anchors"], config=config)
    if (base["graph_sha256"], base["lambda2"], len(base["main_component"])) != (
            pin["baseline_graph_sha256"], pin["baseline_lambda2"], pin["baseline_main_component_size"]):
        raise Refused("c-baseline-drift")
    result = st.connectivity_admission(variant_nodes, variant_edges, base, mapping=mapping, config=config)
    return {"schema_version": SCHEMA + ".c_admission", "c_version": pin["version"], "form": pin["form"],
            "code_sha256": STRUCT_CODE_SHA256, "config_sha256": pin["config_sha256"], "mapping": mapping,
            "result": result, "editor_visible": False}


# ---------------------------------------------------------------- spectral profile

def spectral_profile(nodes, edges, anchors, mapping=None, pin=None):
    """Private descriptor receipt with the exact 09c measurement code; deterministic."""
    o9, mp = load_spectral()
    st = load_struct()
    import numpy as np
    if len(list(nodes)) != len(set(nodes)):
        raise Refused("duplicate-node")
    nodes = sorted(nodes)
    edges = mp.canonical_edges(nodes, edges)
    lwcc = sorted(o9.weak_components(nodes, edges)[0])
    lset = set(lwcc)
    ledges = [(s, t) for s, t in edges if s in lset and t in lset]
    mapping = mapping or {}
    anchors_img = sorted({mapping.get(a, a) for a in anchors})
    T = st.anchored_reachability(nodes, edges, anchors_img, depth=3)
    profile = {
        "retrieval_no_faq_lwcc": {"nodes": len(lwcc), "edges": len(ledges),
                                  "graph_sha256": st.graph_digest(lwcc, ledges),
                                  "spectra": o9.spectra(mp, lwcc, ledges, Q_SWEEP, subspace=True),
                                  "normalized_undirected_lambda2": o9.normalized_lambda2(lwcc, ledges),
                                  "controls": o9.degree_controls(lwcc, ledges)},
        "retrieval_no_faq": {"nodes": len(nodes), "edges": len(edges), "graph_sha256": st.graph_digest(nodes, edges),
                             "spectra": o9.spectra(mp, nodes, edges, [0.0, 0.25], subspace=False),
                             "controls": o9.degree_controls(nodes, edges)},
        "T_descriptor": {k: T[k] for k in ("version", "depth", "value", "reached_count", "main_component_size",
                                           "profile", "mean_forward_distance", "anchors_present", "anchors_missing")},
    }
    runtime = {"python": sys.version.split()[0], "numpy": np.__version__, "platform": platform.platform(),
               "OPENBLAS_NUM_THREADS": os.environ.get("OPENBLAS_NUM_THREADS"),
               "OMP_NUM_THREADS": os.environ.get("OMP_NUM_THREADS"), "solver": "numpy.linalg.eigh full, complex128"}
    return {"schema_version": SCHEMA + ".spectral_profile", "pin": pin or spectral_pin(),
            "script_09c_file_sha256": dcv.file_hash(SCRIPT_09C), "profile": profile,
            "profile_sha256": sha256_bytes(canon(profile)), "runtime": runtime, "editor_visible": False}


def f_reference_check(receipt):
    """Sprint 10b: applies when the LWCC graph equals 09c F's (any scope); values must then be exact."""
    lw = receipt["profile"]["retrieval_no_faq_lwcc"]
    l1 = lw["spectra"]["0.25"]["lambda1"]
    l2 = lw["normalized_undirected_lambda2"]
    return {"applies": lw["graph_sha256"] == F_REFERENCE_LWCC["lwcc_graph_sha256"],
            "retrieval_graph_equals_09c_F": receipt["profile"]["retrieval_no_faq"]["graph_sha256"] == F_REFERENCE["graph_sha256"],
            "lwcc_edges_equal": lw["edges"] == F_REFERENCE_LWCC["lwcc_edges"],
            "lambda1_q0.25": l1, "lambda1_exact": l1 == F_REFERENCE["lambda1_q0.25"],
            "normalized_lambda2": l2, "normalized_lambda2_exact": l2 == F_REFERENCE["normalized_lambda2"],
            "lwcc_nodes_equal": lw["nodes"] == F_REFERENCE["lwcc_nodes"],
            "rounded_6": [round(l1, 6), round(l2, 6)]}


def spectral_delta(receipt, base):
    def flat(r):
        lw, rt = r["profile"]["retrieval_no_faq_lwcc"], r["profile"]["retrieval_no_faq"]
        out = {f"lwcc.{q}.lambda{i}": lw["spectra"][q][f"lambda{i}"] for q in lw["spectra"] if q[0].isdigit()
               for i in (1, 2, 3)}
        out["lwcc.subspace_polarization_v1"] = lw["spectra"]["subspace_polarization_v1"]["value"]
        out["lwcc.normalized_lambda2"] = lw["normalized_undirected_lambda2"]
        out.update({f"lwcc.{k}": v for k, v in lw["controls"].items() if isinstance(v, (int, float))})
        out.update({f"retrieval.{k}": v for k, v in rt["controls"].items() if isinstance(v, (int, float))})
        out["T"] = r["profile"]["T_descriptor"]["value"]
        return out
    a, b = flat(receipt), flat(base)
    return {k: (a[k] - b[k]) if None not in (a[k], b[k]) else None for k in sorted(a)}


# ---------------------------------------------------------------- launch

def namespace_for(manifest, arm, replicate):
    if arm not in ARMS or not isinstance(replicate, int) or not 1 <= replicate <= 99:
        raise Refused("arm-or-replicate")
    root = Path(manifest["output_namespace"]["root"]).resolve()
    ns = (root / "runs" / manifest["run_id"] / f"{arm}-r{replicate:02d}").resolve()
    if root not in ns.parents:
        raise Refused("namespace-outside-root")
    if ns.exists():
        raise Refused("namespace-exists-no-retry")
    return ns


def launch(manifest_path, arm, replicate, *, editor=None, live=False, driver=None):
    """Run one arm/replicate end to end.

    Sprint 10 fixtures pass an in-process `editor`. Sprint 11 binds the container
    runtime: a fixture passes a scripted `driver`; `live=True` builds the Codex
    driver from the manifest's runtime block and refuses while any field is
    UNRESOLVED.
    """
    manifest_path = Path(manifest_path)
    manifest = read_json(manifest_path)
    vm = validate_manifest(manifest, live=live)
    if live and arm in EDITED:
        if driver is not None or editor is not None:
            raise Refused("live-launch-builds-its-own-driver")
        auth = os.environ.get(AUTH_ENV)
        if not auth or not Path(auth).is_file():
            raise Refused(f"live-launch-needs-{AUTH_ENV}")
        rt = manifest["runtime"]
        prompt = read_json(manifest["templates"]["path"])["arms"][arm]["prompt"]
        driver = load_sandbox().CodexDriver(model=rt["model"], reasoning_effort=rt["reasoning_effort"],
                                            prompt=resolve(prompt["path"]).read_bytes(), auth_path=Path(auth))
    if driver is not None and "runtime" not in manifest:
        raise Refused("container-driver-needs-runtime-block")
    if driver is not None and editor is not None:
        raise Refused("editor-and-driver-both-given")
    if (arm in EDITED) != (editor is not None or driver is not None):
        raise Refused("editor-required-exactly-for-R0-and-L")
    gm = dcv.pinned(vm)
    ns = namespace_for(manifest, arm, replicate)
    templates = read_json(manifest["templates"]["path"])
    prompt = templates["arms"][arm].get("prompt")
    host = Host(manifest_path, manifest, arm, replicate, ns, prompt["sha256"] if prompt else None)
    host.event("launch", {"run_id": manifest["run_id"], "arm": arm, "replicate": replicate,
                          "manifest_sha256": dcv.file_hash(manifest_path), "session_id": host.session_id})
    tree = ns / "editor" / "source"
    quiet(dcv.copy_baseline, SimpleNamespace(manifest=Path(manifest["variant_manifest"]["path"]), dest=tree))
    baseline_id = dcv.tree_hash(dcv.inventory(tree))
    host.parent = baseline_id  # first checkpoint's parent is the fresh baseline copy
    state = {"schema_version": SCHEMA + ".run_state", "run_id": manifest["run_id"], "arm": arm, "replicate": replicate,
             "session_id": host.session_id, "baseline_copy_tree_sha256": baseline_id, "flags": [],
             "manifest_sha256": dcv.file_hash(manifest_path), "prompt_sha256": host.prompt_sha256}
    terminal = None
    if arm == "O":
        state.update(status="completed", editor=None, terminal_source="baseline_copy")
        terminal_tree = tree
    else:
        if arm == "S":
            terminal = host.checkpoint(tree, "noop", "sham: unedited copy")
            state.update(status="completed", editor=None)
        elif driver is not None:
            terminal, status = container_session(manifest, arm, vm, host, tree, driver, state)
            state["status"] = status
            if terminal == baseline_id and status == "completed":
                state["status"] = "no_op"
        else:
            session = EditorSession(tree, manifest["limits"], manifest["allowed"]["scope"][arm], host,
                                    corpus_scope=dcv.scope_of(vm))
            try:
                editor(session)
                if not session.finished:
                    raise RuntimeError("editor-returned-without-finish")
                terminal = host.checkpoint(tree, "candidate", "host: tree at finish")
                status = "completed"
            except BudgetStop as stop:
                residual = host.checkpoint(tree, "rejected", f"host: tree at budget stop ({stop})")
                state["budget_stop_residual"] = residual
                terminal = session.checkpoints[-1] if session.checkpoints else None
                status = "budget_stop"
            except Exception as error:  # an editing outcome, never retried
                host.event("editor_failed", {"error": f"{type(error).__name__}: {error}"})
                status = "failed"
            state.update(status=status, editor_tool_calls=session.calls, editor_model_calls=session.model_calls, editor_edits=session.edits,
                         editor_tokens=session.tokens, editor_checkpoints=session.checkpoints,
                         editor_visible_responses=len(session.visible))
            if terminal == baseline_id and status == "completed":
                state["status"] = "no_op"
        state["terminal_checkpoint"] = terminal
        state["terminal_selection"] = manifest["stopping"]["terminal_selection"]
        terminal_tree = host.checkpoint_source(terminal) if terminal else None
    receipts = ns / "host" / "receipts"
    if any(":non_page_file:" in f or ":excluded_class_path:" in f or ":tree_violation:" in f for f in state["flags"]):
        terminal_tree = None  # Sprint 11: no graph receipts for a tree the host will not admit
    if terminal_tree is not None:
        base_list = baseline_pages(gm, vm)
        var_list, nodes, edges = tree_graph(gm, terminal_tree, dcv.scope_of(vm))
        mapping = derive_mapping(base_list, var_list)
        state["receipts"] = {"mapping": write_new(receipts / "mapping.json", mapping)}
        if arm in EDITED:
            if mapping["unmapped_removals"]:
                state["flags"].append("preservation_flagged:unmapped_removal")
            if mapping["partial_unit_loss"]:
                state["flags"].append("preservation_review:partial_unit_loss")
            c = c_admission(manifest, gm, base_list, nodes, edges, mapping["mapping"])
            state["receipts"]["c_admission"] = write_new(receipts / "c-admission.json", c)
            state["c_passed"] = c["result"]["passed"]
            if state["status"] in ("completed", "no_op"):
                if not c["result"]["passed"]:
                    state["status"] = "c_rejected"
                elif mapping["unmapped_removals"]:
                    state["status"] = "preservation_flagged"
        spectral = spectral_profile(nodes, edges, manifest["c_pin"]["anchors"], mapping["mapping"], manifest["spectral_pin"])
        state["receipts"]["spectral_profile"] = write_new(receipts / "spectral-profile.json", spectral)
        state["profile_sha256"] = spectral["profile_sha256"]
        exports = ns / "exports"
        quiet(dcv.review, SimpleNamespace(manifest=Path(manifest["variant_manifest"]["path"]),
                                          candidate=terminal_tree, output=exports / "preservation-review" / "review.json"))
        write_new(exports / "preservation-review" / "mapping.json", mapping)
        state["receipts"]["preservation_review"] = dcv.file_hash(exports / "preservation-review" / "review.json")
        state["receipts"]["support_mapping"] = write_new(exports / "support-mapping.json", support_mapping(mapping, var_list, base_list))
        state["terminal_tree"] = str(terminal_tree)
    eligible = state["status"] == "completed" or (
        state["status"] == "budget_stop" and manifest["stopping"]["budget_stop_terminal_eligible_for_grading"] is True)
    state["eligible_for_grading"] = bool(eligible and terminal_tree is not None)
    host.event("terminal", {"status": state["status"], "terminal": terminal})
    state["transcript_sha256"] = host.transcript_sha256()
    state["events_sha256"] = dcv.file_hash(host.private / "events.jsonl")
    write_new(host.private / "run-state.json", state)
    return state


def container_session(manifest, arm, vm, host, tree, driver, state):
    """Sprint 11: one editor session in the container runtime; fills `state`, returns (terminal, status)."""
    es = load_sandbox()
    rt = manifest["runtime"]
    state["editor_runtime"] = "container"
    try:
        out = es.run_session(host=host, tree=tree, limits=manifest["limits"], arm_scope=manifest["allowed"]["scope"][arm],
                             corpus_scope=dcv.scope_of(vm), excluded_class=dcv.excluded_class, driver=driver,
                             editor_image=rt["editor_image"]["ref"], proxy_image=rt["proxy_image"]["ref"],
                             snapshot_seconds=rt["snapshot_seconds"])
    except Exception as error:  # an editing or runtime failure, never retried
        host.event("editor_failed", {"error": f"{type(error).__name__}: {error}"[:500]})
        state["editor_checkpoints"] = []
        return None, "failed"
    counts = out["counts"]
    state["flags"].extend(out["flags"])
    state.update(editor_driver=out["driver"], editor_model_calls=counts["model_calls"],
                 editor_tool_calls=counts["tool_calls"], editor_tokens=counts["tokens"], editor_edits=out["edits"],
                 editor_checkpoints=out["checkpoints"], editor_wall_seconds=out["wall_seconds"],
                 editor_exit_code=out.get("exit_code"), editor_thread_id=counts["thread_id"],
                 editor_errors=counts["errors"], editor_stop=out["stop"])
    if out.get("residual"):
        state["budget_stop_residual" if out["status"] == "budget_stop" else "residual_checkpoint"] = out["residual"]
    return out["terminal"], out["status"]


def support_mapping(mapping, var_list, base_list):
    """Doc-level baseline -> variant translation for later support/citation remapping."""
    var = {p.doc_id: p for p in var_list}
    table = {}
    for p in base_list:
        d = p.doc_id
        entry = mapping["translation"].get(d)
        if entry is None:
            table[d] = {"status": "unchanged", "variant_ids": [d]}
            continue
        ids = ([d] if entry["kept"] else []) + [h["doc_id"] for h in entry["holders"] if h["doc_id"] != d]
        status = "changed" if entry["kept"] else ("mapped" if entry.get("survivor") else "unmapped")
        table[d] = {"status": status, "variant_ids": ids, "survivor": entry.get("survivor"), "coverage": entry["coverage"]}
    return {"schema_version": SCHEMA + ".support_mapping", "rule": mapping["rule"],
            "variant_file_sha256": {d: sha256_bytes(p.text.encode("utf-8")) for d, p in sorted(var.items())},
            "baseline_to_variant": table, "added_pages": mapping["added"],
            "note": "doc-level translation from moved content; citation lines are remapped against variant bytes in Sprint 12; contains no question material",
            "certifies_semantic_equivalence": False}


# ---------------------------------------------------------------- grading export

def grading_export(manifest_path, namespaces, dest, key_path):
    """Blind provider datasets per eligible variant; arm key written outside the packet."""
    manifest = read_json(manifest_path)
    validate_manifest(manifest, live=False)
    root = Path(manifest["output_namespace"]["root"]).resolve()
    dest, key_path = Path(dest).resolve(), Path(key_path).resolve()
    if root not in dest.parents or dest.exists() or dest in key_path.parents or key_path == dest:
        raise Refused("grading-export-path")
    rows = []
    for ns in namespaces:
        state = read_json(Path(ns) / "host" / "run-state.json")
        if not state["eligible_for_grading"]:
            continue
        rows.append(state)
    order = list(range(len(rows)))
    secrets.SystemRandom().shuffle(order)
    ids = set()
    key = {"schema_version": SCHEMA + ".grading_key", "packet": str(dest), "variants": {}}
    dest.mkdir(parents=True)
    for i in order:
        state = rows[i]
        opaque = "v-" + secrets.token_hex(6)
        while opaque in ids:
            opaque = "v-" + secrets.token_hex(6)
        ids.add(opaque)
        target = dest / "variants" / opaque
        quiet(dcv.export_agent, SimpleNamespace(manifest=Path(manifest["variant_manifest"]["path"]),
                                                candidate=Path(state["terminal_tree"]), dest=target))
        receipt = target / "export-receipt.json"
        private_receipt = key_path.parent / "export-receipts" / f"{opaque}.json"
        private_receipt.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(receipt), private_receipt)  # it names the source tree hash
        key["variants"][opaque] = {"arm": state["arm"], "replicate": state["replicate"], "run_id": state["run_id"],
                                   "status": state["status"], "terminal_checkpoint": state.get("terminal_checkpoint"),
                                   "corpus_tree_sha256": dcv.tree_hash(dcv.inventory(target / "corpus")),
                                   "graph_file_sha256": dcv.file_hash(target / "graph_edges.csv")}
    packet = {"schema_version": SCHEMA + ".grading_packet", "variants": sorted(ids),
              "contains": ["corpus/<doc_id>.md", "graph_edges.csv"], "labels": "opaque; arm key held outside packet",
              "contains_evaluation_material": False}
    write_new(dest / "packet.json", packet)
    key_sha = write_new(key_path, key)
    return {"packet": str(dest), "variants": len(ids), "key_sha256": key_sha,
            "packet_sha256": dcv.file_hash(dest / "packet.json")}


def tree_bytes(path: Path):
    return {p.relative_to(path).as_posix(): p.read_bytes() for p in sorted(path.rglob("*")) if p.is_file()}


# ---------------------------------------------------------------- CLI

def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    m = sub.add_parser("manifest", help="write a run manifest (fixture or live template)")
    m.add_argument("--run-id", required=True)
    m.add_argument("--variant-manifest", type=Path, required=True)
    m.add_argument("--mode", choices=("fixture", "live"), default="fixture")
    m.add_argument("--output", type=Path, required=True)
    m.add_argument("--created", required=True)
    v = sub.add_parser("validate")
    v.add_argument("--manifest", type=Path, required=True)
    v.add_argument("--live", action="store_true")
    args = parser.parse_args()
    if args.command == "manifest":
        limits = FIXTURE_LIMITS if args.mode == "fixture" else None
        data = build_manifest(run_id=args.run_id, variant_manifest_path=args.variant_manifest, mode=args.mode,
                              fixture_limits=limits, created=args.created)
        print(json.dumps({"manifest_sha256": write_new(args.output, data), "unresolved": unresolved_paths(data)}, indent=1))
    else:
        manifest = read_json(args.manifest)
        try:
            validate_manifest(manifest, live=args.live)
            print(json.dumps({"valid": True, "live": args.live, "unresolved": unresolved_paths(manifest)}, indent=1))
        except Refused as error:
            print(json.dumps({"valid": False, "live": args.live, "refusal": str(error)}, indent=1))
            raise SystemExit(1)


if __name__ == "__main__":
    main()
