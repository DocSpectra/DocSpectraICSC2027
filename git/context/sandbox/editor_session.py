"""Sprint 11: run one editor session in containers and hand Sprint 10 its checkpoints.

The runner seeds a fresh container tree from the host baseline copy, checks the
two are equal, runs a driver (Codex, or a scripted shell editor for fixtures),
streams the driver's JSONL events into the private transcript, and checkpoints
the tree at every turn.completed and at the end. Edits are net changed paths
against the baseline, from host snapshots. Limits stop the session; partial
trees are kept. A session is never resumed: Codex runs `--ephemeral`, and a new
arm or replicate is a new namespace with new containers.

Terminal outcomes (Sprint 10 states):
  completed / no_op      the driver exited 0 within limits
  budget_stop            a limit stopped it; terminal = last turn checkpoint
  failed                 non-zero exit, or an infrastructure stop (usage or
                         rate limit), flagged `infrastructure_stop:<kind>`
  preservation_flagged   the final tree holds a non-page file, an excluded-class
                         path or a non-regular member, or an L terminal adds or
                         removes pages; recorded, never repaired, not admitted
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import codex_events
import editor_container
import tree_io

SANDBOX = Path(__file__).resolve().parent
CODEX_ARGV = ["sh", "-c", "cd /work && exec codex exec --json --skip-git-repo-check --ephemeral --ignore-rules "
                          "--color never -"]


class CodexDriver:
    """Real Codex. The author supplies auth.json (path on the host, outside the workspace)."""

    def __init__(self, *, model, reasoning_effort, prompt: bytes, auth_path: Path,
                 config_template: Path = SANDBOX / "editor/config.template.toml"):
        self.model, self.effort, self.prompt = model, reasoning_effort, prompt
        self.auth_path, self.template = Path(auth_path), Path(config_template)

    def config_bytes(self) -> bytes:
        text = self.template.read_text(encoding="utf-8")
        return text.replace("{model}", self.model).replace("{reasoning_effort}", self.effort).encode()

    def identity(self):
        return {"driver": "codex-exec", "argv": CODEX_ARGV, "model": self.model, "reasoning_effort": self.effort}

    def execute(self, container, on_line, wall_seconds, stop):
        container.put_file("/codex-home/config.toml", self.config_bytes())
        container.put_file("/codex-home/auth.json", self.auth_path.read_bytes())
        return container.run(CODEX_ARGV, self.prompt, on_line, wall_seconds, stop)


class ScriptedShellDriver:
    """Fixture editor: shell writes and deletes inside the container, no model.

    `batches` is a list of lists of ("write", rel, bytes) / ("delete", rel). Each
    operation is one shell command in the container; each batch ends with a
    turn.completed boundary (zero usage), which makes the host checkpoint.
    """

    def __init__(self, batches):
        self.batches = batches

    def identity(self):
        return {"driver": "scripted-shell-v1", "model": None,
                "batches": [[op[:2] for op in batch] for batch in self.batches]}

    def execute(self, container, on_line, wall_seconds, stop):
        on_line(json.dumps({"type": "thread.started", "thread_id": "scripted"}))
        for n, batch in enumerate(self.batches, 1):
            for i, op in enumerate(batch):
                if stop.is_set():
                    return 137, "stop_requested"
                if op[0] == "write":
                    script = 'mkdir -p "$(dirname "$0")" && cat > "$0"'
                    result = editor_container.docker("exec", "-i", container.editor, "sh", "-c",
                                                     f"cd /work && {script}", op[1], input=op[2], check=False)
                elif op[0] == "delete":
                    result = editor_container.docker("exec", container.editor, "sh", "-c", 'cd /work && rm -- "$0"',
                                                     op[1], check=False)
                else:
                    raise ValueError(f"unknown-op:{op[0]}")
                on_line(json.dumps({"type": "item.completed", "item": {
                    "id": f"b{n}-{i}", "type": "command_execution", "command": f"{op[0]} {op[1]}",
                    "exit_code": result.returncode, "status": "completed" if result.returncode == 0 else "failed"}}))
                if result.returncode != 0:
                    return result.returncode, None
            on_line(json.dumps({"type": "turn.completed", "usage": {"input_tokens": 0, "output_tokens": 0}}))
        return 0, None


def tree_problems(baseline: dict, final: dict, arm_scope: str, corpus_scope: str, excluded_class):
    """Flags for a terminal tree the host will not admit (reported, never repaired)."""
    flags = []
    non_pages = sorted(p for p in final if not p.endswith(".md"))
    if non_pages:
        flags.append("preservation_flagged:non_page_file:" + ",".join(non_pages[:5]))
    excluded = sorted(p for p in final if excluded_class(p, corpus_scope))
    if excluded:
        flags.append("preservation_flagged:excluded_class_path:" + ",".join(excluded[:5]))
    if arm_scope == "links_only_existing_paths" and set(final) != set(baseline):
        flags.append("preservation_flagged:L_scope_page_added_or_removed")
    return flags


def run_session(*, host, tree: Path, limits: dict, arm_scope: str, corpus_scope: str, excluded_class, driver,
                editor_image: str, proxy_image: str | None, snapshot_seconds: float = 120.0):
    """Run one session; returns the outcome dict used by restructuring_runs.launch."""
    baseline = tree_io.read_host_tree(tree)
    logs = host.private / "container"
    container = editor_container.EditorContainer(host.session_id, editor_image, proxy_image, logs)
    counter = codex_events.EventCounter(limits)
    stop = threading.Event()
    lock = threading.Lock()
    out = {"checkpoints": [], "flags": [], "stop": None, "edits": 0, "driver": driver.identity(),
           "container_names": {"editor": container.editor, "proxy": container.proxy if proxy_image else None}}
    started = time.monotonic()

    def take(reason):
        """Turn checkpoint; a tree over the edit cap is not stored as a candidate."""
        with lock:
            snap = container.snapshot()
            out["edits"] = tree_io.edit_count(tree_io.diff(baseline, snap))
            counter.check(edits=out["edits"])
            tree_io.mirror(snap, tree)
            return host.checkpoint(tree, "candidate", reason)

    def limit_stop(error):
        if out["stop"] is None:
            out["stop"] = error.key
            host.event("budget_stop", {"limit": error.key, "value": error.value})
        stop.set()

    def on_line(line):
        host.transcript({"codex_event": line})
        try:
            event = counter.feed_line(line)
            if event and event.get("type") == "turn.completed":
                out["checkpoints"].append(take(f"turn {counter.turns_completed} completed"))
        except codex_events.LimitReached as error:
            limit_stop(error)
        except tree_io.TreeViolation as error:
            host.event("checkpoint_invalid", {"kind": error.kind})

    def periodic():
        while not stop.wait(snapshot_seconds):
            try:
                with lock:
                    snap = container.snapshot()
                    out["edits"] = tree_io.edit_count(tree_io.diff(baseline, snap))
                counter.check(edits=out["edits"])
            except codex_events.LimitReached as error:
                limit_stop(error)
            except (tree_io.TreeViolation, editor_container.DockerError):
                pass  # judged at the end

    try:
        container.start(baseline)
        if container.snapshot() != baseline:
            raise RuntimeError("fresh-container-tree-differs-from-baseline-copy")
        host.event("container_started", {"names": out["container_names"], "files": len(baseline)})
        watcher = threading.Thread(target=periodic, daemon=True)
        watcher.start()
        code, why = driver.execute(container, on_line, limits.get("wall_seconds") if isinstance(
            limits.get("wall_seconds"), (int, float)) else None, stop)
        stop.set()
        if why == "wall_seconds" and out["stop"] is None:
            out["stop"] = "wall_seconds"
            host.event("budget_stop", {"limit": "wall_seconds", "value": round(time.monotonic() - started, 1)})
        out["exit_code"] = code
        try:
            with lock:
                final = container.snapshot()
        except tree_io.TreeViolation as error:
            final = None
            out["flags"].append(f"preservation_flagged:tree_violation:{error.kind}")
        logs.mkdir(parents=True, exist_ok=True)
        (logs / "proxy.log").write_bytes(container.proxy_log())
        (logs / "docker-inspect.json").write_bytes(container.inspect())
    finally:
        stop.set()
        container.teardown()

    out["counts"] = counter.summary()
    out["wall_seconds"] = round(time.monotonic() - started, 1)
    if final is None:
        out["status"], out["terminal"] = "preservation_flagged", None
        return out
    out["edits"] = tree_io.edit_count(tree_io.diff(baseline, final))
    tree_io.mirror(final, tree)
    if counter.infrastructure_stop:
        out["flags"].append(f"infrastructure_stop:{counter.infrastructure_stop}")
        out["residual"] = host.checkpoint(tree, "failed", "host: tree at infrastructure stop")
        out["status"], out["terminal"] = "failed", None
    elif out["stop"]:
        out["residual"] = host.checkpoint(tree, "rejected", f"host: tree at budget stop ({out['stop']})")
        out["status"] = "budget_stop"
        out["terminal"] = out["checkpoints"][-1] if out["checkpoints"] else None
    elif code != 0:
        out["residual"] = host.checkpoint(tree, "failed", f"host: tree at editor exit {code}")
        out["status"], out["terminal"] = "failed", None
    else:
        out["terminal"] = host.checkpoint(tree, "candidate", "host: tree at finish")
        out["status"] = "completed"
    problems = tree_problems(baseline, final, arm_scope, corpus_scope, excluded_class)
    if problems:
        out["flags"].extend(problems)
        if out["status"] == "completed":
            out["status"] = "preservation_flagged"
    return out


# ------------------------------------------------------------ runtime identity (run manifest block)

CODEX_VERSION = "0.161.0"
PINNED_FILES = ("editor/Dockerfile", "editor/config.template.toml", "proxy/Dockerfile", "proxy/allowlist.txt",
                "proxy/allowlist_proxy.py", "tree_io.py", "codex_events.py", "editor_container.py", "editor_session.py")


def file_sha256(path: Path) -> str:
    import hashlib
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def runtime_block(*, model, reasoning_effort, driver, editor_image="docspectra/codex-editor:v1",
                  proxy_image="docspectra/egress-proxy:v1", grading_consequence="UNRESOLVED"):
    """The frozen runtime identity; launch re-checks every pin before starting containers."""
    return {"version": "codex_container_v1", "driver": driver, "codex_version": CODEX_VERSION,
            "model": model, "reasoning_effort": reasoning_effort,
            "editor_image": {"ref": editor_image, "id": editor_container.image_id(editor_image)},
            "proxy_image": {"ref": proxy_image, "id": editor_container.image_id(proxy_image)},
            "files_sha256": {name: file_sha256(SANDBOX / name) for name in PINNED_FILES},
            "codex_argv": CODEX_ARGV, "snapshot_seconds": 120,
            "auth": "author-supplied auth.json copied into the session's tmpfs CODEX_HOME; readable by the editor's shell",
            "resume": "never (codex --ephemeral; a new arm or replicate is a new namespace and new containers)",
            "infrastructure_stop": {"policy": "usage or rate limit -> status failed, flag infrastructure_stop:<kind>, never resumed",
                                    "grading_consequence": grading_consequence},
            "security_testing": "waived by the author on 2026-10-08 (reviews/2026-10-08-author-decisions-editor-runtime.md); "
                                "configuration set, not attack-tested"}


def check_runtime_block(block):
    """Refusal reasons for a runtime block whose pins no longer match the files or images."""
    problems = [f"runtime-file-drift:{n}" for n, sha in block["files_sha256"].items()
                if not (SANDBOX / n).is_file() or file_sha256(SANDBOX / n) != sha]
    for key in ("editor_image", "proxy_image"):
        try:
            if editor_container.image_id(block[key]["ref"]) != block[key]["id"]:
                problems.append(f"runtime-image-drift:{key}")
        except editor_container.DockerError:
            problems.append(f"runtime-image-missing:{key}")
    if block["codex_argv"] != CODEX_ARGV:
        problems.append("runtime-argv-drift")
    return problems
