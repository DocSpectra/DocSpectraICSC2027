"""Sprint 11 runtime checks. No model and no network: event streams are synthetic
(they test the contract only, not Codex's real output), and container tests use
the scripted shell driver on the small Sprint 10 fixture tree."""
import io
import json
import os
import shutil
import subprocess
import tarfile
import unittest
from unittest.mock import patch

import restructuring_runs as rr
import test_restructuring_runs as trr

es = rr.load_sandbox()
import codex_events  # noqa: E402  (on sys.path via load_sandbox)
import tree_io  # noqa: E402

V6 = rr.CONTEXT / "evidence/docker-corpus-variant-input-v6-docs-scope-2026-10-07.json"


def docker_ready():
    try:
        return subprocess.run(["docker", "image", "inspect", "docspectra/codex-editor:v1"],
                              capture_output=True, timeout=60).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


DOCKER = docker_ready()


class Events(unittest.TestCase):
    def stream(self, *events):
        return [json.dumps(e) for e in events]

    def test_counts_tokens_and_turns(self):
        c = codex_events.EventCounter({"model_calls": 5, "tool_calls": 10, "tokens": 1000})
        for line in self.stream({"type": "thread.started", "thread_id": "t-1"}, {"type": "turn.started"},
                                {"type": "item.completed", "item": {"type": "command_execution"}},
                                {"type": "item.completed", "item": {"type": "agent_message"}},
                                {"type": "item.completed", "item": {"item_type": "file_change"}},
                                {"type": "turn.completed", "usage": {"input_tokens": 300, "cached_input_tokens": 200,
                                                                      "output_tokens": 50}}):
            c.feed_line(line)
        c.feed_line("not json")
        s = c.summary()
        self.assertEqual((s["model_calls"], s["tool_calls"], s["tokens"], s["turns_completed"], s["thread_id"],
                          s["non_json_lines"]), (1, 2, 350, 1, "t-1", 1))

    def test_token_limit_is_checked_at_turn_end(self):
        c = codex_events.EventCounter({"tokens": 100})
        c.feed({"type": "turn.started"})
        with self.assertRaises(codex_events.LimitReached) as hit:
            c.feed({"type": "turn.completed", "usage": {"input_tokens": 90, "output_tokens": 20}})
        self.assertEqual((hit.exception.key, hit.exception.value), ("tokens", 110))

    def test_model_call_and_edit_limits(self):
        c = codex_events.EventCounter({"model_calls": 0, "edits": 2})
        with self.assertRaises(codex_events.LimitReached):
            c.feed({"type": "turn.started"})
        c = codex_events.EventCounter({"edits": 2})
        c.check(edits=2)
        with self.assertRaises(codex_events.LimitReached):
            c.check(edits=3)

    def test_usage_limit_is_an_infrastructure_stop(self):
        c = codex_events.EventCounter({})
        c.feed({"type": "turn.failed", "error": {"message": "You've hit your usage limit. Try again later."}})
        self.assertEqual(c.summary()["infrastructure_stop"], "usage_limit")
        c = codex_events.EventCounter({})
        c.feed({"type": "error", "message": "stream disconnected"})
        self.assertIsNone(c.summary()["infrastructure_stop"])

    def test_unavailable_tool_is_an_infrastructure_stop_not_a_no_op(self):
        c = codex_events.EventCounter({})
        c.feed({"type": "item.completed", "item": {"type": "error", "message":
                "Code Mode is unavailable because code-mode host is disabled."}})
        self.assertEqual(c.summary()["infrastructure_stop"], "tool_unavailable")
        self.assertEqual(c.summary()["tool_calls"], 0)


class TreeTransfer(unittest.TestCase):
    def tar(self, add):
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w") as archive:
            add(archive)
        buffer.seek(0)
        return buffer

    def test_round_trip(self):
        files = {"a.md": b"# A\n", "d/e/b.md": b"# B\n"}
        self.assertEqual(tree_io.read_snapshot(io.BytesIO(tree_io.write_tar(files, 10001, 10001))), files)

    def test_non_regular_members_and_unsafe_names_are_refused(self):
        def link(kind):
            def add(archive):
                info = tarfile.TarInfo("x.md")
                info.type, info.linkname = kind, "/etc/passwd"
                archive.addfile(info)
            return add
        for kind, label in ((tarfile.SYMTYPE, "nonregular-symlink"), (tarfile.LNKTYPE, "nonregular-hardlink")):
            with self.assertRaisesRegex(tree_io.TreeViolation, label):
                tree_io.read_snapshot(self.tar(link(kind)))
        for name in ("../up.md", "/abs.md", "c:/x.md"):
            def add(archive, name=name):
                archive.addfile(tarfile.TarInfo(name), io.BytesIO(b""))
            with self.assertRaisesRegex(tree_io.TreeViolation, "unsafe-name"):
                tree_io.read_snapshot(self.tar(add))

    def test_tree_problems(self):
        base = {"a.md": b"", "b.md": b""}
        excluded = lambda p, scope: "agent_instructions" if p.lower().endswith("agents.md") else None
        self.assertEqual(es.tree_problems(base, {"a.md": b"x", "b.md": b""}, "links_only_existing_paths", "s", excluded), [])
        flags = es.tree_problems(base, {"a.md": b"", "c.md": b""}, "links_only_existing_paths", "s", excluded)
        self.assertEqual(flags, ["preservation_flagged:L_scope_page_added_or_removed"])
        flags = es.tree_problems(base, {"a.md": b"", "notes.txt": b"", "x/AGENTS.md": b""}, "restructure", "s", excluded)
        self.assertEqual(flags, ["preservation_flagged:non_page_file:notes.txt",
                                 "preservation_flagged:excluded_class_path:x/AGENTS.md"])


@unittest.skipUnless(DOCKER, "docker or docspectra/codex-editor:v1 unavailable")
class ContainerRuns(unittest.TestCase):
    setUp, tearDown, ns, make_manifest = trr.Runs.setUp, trr.Runs.tearDown, trr.Runs.ns, trr.Runs.make_manifest

    def manifest_with_runtime(self, run_id, limits=rr.FIXTURE_LIMITS, **extra):
        runtime = es.runtime_block(model=None, reasoning_effort=None, driver="scripted-shell-v1")
        data = rr.build_manifest(run_id=run_id, variant_manifest_path=self.variant_manifest, mode="fixture",
                                 anchors=[self.ids["index.md"]], fixture_limits=limits, created="2026-10-08",
                                 runtime=runtime, **extra)
        path = self.root / f"{run_id}.json"
        rr.write_new(path, data)
        return path

    def split_merge(self):
        """The Sprint 10 link-preserving split plus mapped merge, as shell batches."""
        merged = ("# Delta\nDelta covers storage drivers.\nDelta notes the overlay limit.\nDelta lists volume flags.\n"
                  "Read [phi](f.md).\n\n## Epsilon\nEpsilon covers bind mounts.\nEpsilon warns about permissions.\n")
        return [[("write", "d.md", merged.encode()), ("delete", "e.md")],
                [("write", "c.md", b"# Gamma\nGamma explains networking.\nHome is [index](index.md); see [delta](d.md).\n")]]

    def test_container_split_merge_matches_in_process_editor(self):
        batches = self.split_merge()

        def in_process(session):
            for batch in batches:
                for op in batch:
                    if op[0] == "write":
                        session.write_file(op[1], op[2].decode())
                    else:
                        session.delete_file(op[1])
                session.checkpoint("batch")
            session.finish("done")

        ref = rr.launch(self.manifest, "R0", 1, editor=in_process)
        manifest = self.manifest_with_runtime("container-run")
        got = rr.launch(manifest, "R0", 1, driver=es.ScriptedShellDriver(batches))
        self.assertEqual(got["status"], ref["status"])
        self.assertEqual(got["terminal_checkpoint"], ref["terminal_checkpoint"])
        self.assertEqual(got["profile_sha256"], ref["profile_sha256"])
        self.assertEqual(got["c_passed"], ref["c_passed"])
        self.assertEqual(got["receipts"]["c_admission"], ref["receipts"]["c_admission"])
        self.assertEqual(got["receipts"]["mapping"], ref["receipts"]["mapping"])
        self.assertEqual(got["editor_checkpoints"], ref["editor_checkpoints"])
        self.assertEqual((got["editor_model_calls"], got["editor_edits"], got["editor_tool_calls"]), (0, 3, 3))
        leftover = subprocess.run(["docker", "ps", "-aq", "--filter", "name=ds11-"], capture_output=True, text=True)
        self.assertEqual(leftover.stdout.strip(), "")
        transcript = (self.ns("R0", run_id="container-run") / "host/transcript.jsonl").read_text(encoding="utf-8")
        self.assertIn("turn.completed", transcript)

    def test_l_scope_violation_and_non_page_files_are_flagged_not_admitted(self):
        manifest = self.manifest_with_runtime("l-scope")
        state = rr.launch(manifest, "L", 1, driver=es.ScriptedShellDriver([[("write", "new.md", b"# New\n[a](a.md)\n")]]))
        self.assertEqual(state["status"], "preservation_flagged")
        self.assertIn("preservation_flagged:L_scope_page_added_or_removed", state["flags"])
        self.assertFalse(state["eligible_for_grading"])
        manifest = self.manifest_with_runtime("non-page")
        state = rr.launch(manifest, "R0", 1, driver=es.ScriptedShellDriver([[("write", "notes.txt", b"scratch\n")]]))
        self.assertEqual(state["status"], "preservation_flagged")
        self.assertNotIn("receipts", state)
        self.assertFalse(state["eligible_for_grading"])

    def test_edit_limit_is_a_budget_stop_with_last_turn_terminal(self):
        limits = dict(rr.FIXTURE_LIMITS, edits=2)
        manifest = self.manifest_with_runtime("edit-cap", limits=limits)
        batches = self.split_merge()
        state = rr.launch(manifest, "R0", 1, driver=es.ScriptedShellDriver(batches))
        self.assertEqual(state["status"], "budget_stop")
        self.assertEqual(state["editor_stop"], "edits")
        self.assertEqual(state["terminal_checkpoint"], state["editor_checkpoints"][0])
        self.assertFalse(state["eligible_for_grading"])  # fixture manifest leaves eligibility UNRESOLVED

    def test_live_refusals(self):
        manifest = rr.read_json(self.manifest_with_runtime("refusals"))
        with self.assertRaisesRegex(rr.Refused, "live-launch-refused:mode-not-live"):
            rr.validate_manifest(manifest, live=True)
        live = rr.build_manifest(run_id="live-r", variant_manifest_path=V6, mode="live", created="2026-10-08",
                                
                                 runtime=es.runtime_block(model="m", reasoning_effort="medium", driver="codex-exec"))
        unresolved = rr.unresolved_paths(live)
        self.assertIn("runtime.infrastructure_stop.grading_consequence", unresolved)
        path = self.root / "live-r.json"
        rr.write_new(path, live)
        with self.assertRaisesRegex(rr.Refused, "live-launch-refused"):
            rr.launch(path, "R0", 1, live=True)
        no_runtime = dict(live)
        no_runtime.pop("runtime")
        with self.assertRaisesRegex(rr.Refused, "runtime-block-missing"):
            rr.validate_manifest(no_runtime, live=True)
        drifted = json.loads(json.dumps(live))
        drifted["runtime"]["files_sha256"]["tree_io.py"] = "0" * 64
        with self.assertRaisesRegex(rr.Refused, "runtime-file-drift:tree_io.py"):
            rr.validate_manifest(drifted, live=False)
        drifted = json.loads(json.dumps(live))
        drifted["runtime"]["editor_image"]["id"] = "sha256:" + "0" * 64
        with self.assertRaisesRegex(rr.Refused, "runtime-image-drift:editor_image"):
            rr.validate_manifest(drifted, live=False)
        with self.assertRaisesRegex(rr.Refused, "container-driver-needs-runtime-block"):
            rr.launch(self.manifest, "R0", 1, driver=es.ScriptedShellDriver([]))

    def test_resolved_live_manifest_needs_author_auth(self):
        live = rr.build_manifest(run_id="live-auth", variant_manifest_path=V6, mode="live", created="2026-10-08",
                                
                                 runtime=es.runtime_block(model="m", reasoning_effort="medium", driver="codex-exec"))
        resolved = json.loads(json.dumps(live).replace(json.dumps(rr.UNRESOLVED), "1"))
        path = self.root / "live-auth.json"
        rr.write_new(path, resolved)
        with patch.dict(os.environ, {rr.AUTH_ENV: ""}):
            with self.assertRaisesRegex(rr.Refused, "live-launch-needs-DOCSPECTRA_CODEX_AUTH|fixture-only-values"):
                rr.launch(path, "R0", 1, live=True)


if __name__ == "__main__":
    unittest.main()
