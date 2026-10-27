"""Sprint 11: lifecycle of one editor session's containers (one arm, one replicate).

Per session: a fresh named volume holding the editor tree, an internal network
whose only way out is the egress allowlist proxy, the proxy container, and the
editor container (non-root, no capabilities, read-only root, tmpfs CODEX_HOME,
home and /tmp). The host moves the tree in and out only as tar (tree_io), so it
never opens filesystem objects the editor created. Everything is removed at
teardown; logs and transcripts stay on the host.

Security testing of this configuration was waived by the author on October 8,
2026 (reviews/2026-10-08-author-decisions-editor-runtime.md). This module sets
the configuration; it does not test it.
"""
from __future__ import annotations

import io
import subprocess
import threading
import time
from pathlib import Path

import tree_io

EDITOR_UID = 10001
PROXY_ALIAS = "egress-proxy"
PROXY_URL = f"http://{PROXY_ALIAS}:3128"


class DockerError(RuntimeError):
    pass


def docker(*args, input=None, check=True, timeout=600):
    result = subprocess.run(["docker", *args], input=input, capture_output=True, timeout=timeout)
    if check and result.returncode != 0:
        raise DockerError(f"docker {args[0]} failed: {result.stderr.decode(errors='replace')[:400]}")
    return result


def image_id(ref: str) -> str:
    return docker("image", "inspect", "--format", "{{.Id}}", ref).stdout.decode().strip()


class EditorContainer:
    def __init__(self, session_id: str, editor_image: str, proxy_image: str | None, log_dir: Path):
        short = session_id.replace("-", "")[:12]
        self.name = f"ds11-{short}"
        self.editor_image, self.proxy_image = editor_image, proxy_image
        self.volume = f"{self.name}-work"
        self.internal = f"{self.name}-int"
        self.egress = f"{self.name}-egr"
        self.proxy = f"{self.name}-proxy"
        self.editor = f"{self.name}-editor"
        self.log_dir = log_dir
        self.created: list[tuple[str, str]] = []

    # ------------------------------------------------------------ setup

    def start(self, files: dict[str, bytes]):
        docker("volume", "create", self.volume)
        self.created.append(("volume", self.volume))
        seed = tree_io.write_tar(files, EDITOR_UID, EDITOR_UID)
        docker("run", "--rm", "-i", "--network", "none", "--read-only", "--cap-drop", "ALL",
               "--security-opt", "no-new-privileges", "--user", f"{EDITOR_UID}:{EDITOR_UID}",
               "-v", f"{self.volume}:/work", "--entrypoint", "sh", self.editor_image,
               "-c", "cd /work && tar -x -o -f -", input=seed)
        network = "none"
        env = []
        if self.proxy_image:
            docker("network", "create", "--internal", self.internal)
            self.created.append(("network", self.internal))
            docker("network", "create", self.egress)
            self.created.append(("network", self.egress))
            docker("run", "-d", "--name", self.proxy, "--network", self.egress, "--read-only",
                   "--cap-drop", "ALL", "--security-opt", "no-new-privileges", "--user", "65534:65534",
                   "--pids-limit", "64", "--memory", "256m", self.proxy_image)
            self.created.append(("container", self.proxy))
            docker("network", "connect", "--alias", PROXY_ALIAS, self.internal, self.proxy)
            network = self.internal
            env = ["-e", f"HTTPS_PROXY={PROXY_URL}", "-e", f"HTTP_PROXY={PROXY_URL}",
                   "-e", f"https_proxy={PROXY_URL}", "-e", f"http_proxy={PROXY_URL}", "-e", "NO_PROXY="]
        tmpfs = f"uid={EDITOR_UID},gid={EDITOR_UID}"
        docker("run", "-d", "--name", self.editor, "--network", network, "--read-only", "--cap-drop", "ALL",
               "--security-opt", "no-new-privileges", "--user", f"{EDITOR_UID}:{EDITOR_UID}",
               "--pids-limit", "512", "--memory", "4g",
               "--tmpfs", f"/codex-home:rw,size=64m,mode=0700,{tmpfs}",
               "--tmpfs", f"/home/editor:rw,size=64m,mode=0700,{tmpfs}",
               "--tmpfs", f"/tmp:rw,size=512m,mode=1777",
               "-v", f"{self.volume}:/work", *env, "--entrypoint", "tail", self.editor_image, "-f", "/dev/null")
        self.created.append(("container", self.editor))

    def put_file(self, container_path: str, data: bytes, mode: str = "600"):
        """Write host bytes to a path inside the editor container (stdin; never on argv)."""
        docker("exec", "-i", self.editor, "sh", "-c", f'umask 077 && cat > "$0" && chmod {mode} "$0"',
               container_path, input=data)

    # ------------------------------------------------------------ tree

    def running(self) -> bool:
        out = docker("inspect", "--format", "{{.State.Running}} {{.State.Paused}}", self.editor, check=False)
        return out.stdout.decode().split()[:1] == ["true"]

    def snapshot(self) -> dict[str, bytes]:
        """The editor tree as {relative path: bytes}; raises tree_io.TreeViolation for non-regular members."""
        paused = False
        if self.running():
            docker("pause", self.editor)
            paused = True
        try:
            tar = docker("cp", f"{self.editor}:/work/.", "-").stdout
        finally:
            if paused:
                docker("unpause", self.editor, check=False)
        return tree_io.read_snapshot(io.BytesIO(tar))

    # ------------------------------------------------------------ run

    def run(self, argv: list[str], stdin: bytes, on_line, wall_seconds: float | None, stop_flag: threading.Event):
        """Run argv in the editor container, calling on_line(str) per stdout line.

        Returns (exit_code, stop_reason). The watchdog kills the editor container
        at the wall-clock limit or when stop_flag is set by the caller.
        """
        stderr_path = self.log_dir / "editor-stderr.log"
        self.log_dir.mkdir(parents=True, exist_ok=True)
        with stderr_path.open("ab") as err:
            proc = subprocess.Popen(["docker", "exec", "-i", self.editor, *argv], stdin=subprocess.PIPE,
                                    stdout=subprocess.PIPE, stderr=err)
            proc.stdin.write(stdin)
            proc.stdin.close()
            reason = {"value": None}
            started = time.monotonic()

            def watchdog():
                while proc.poll() is None:
                    if wall_seconds is not None and time.monotonic() - started > wall_seconds:
                        reason["value"] = reason["value"] or "wall_seconds"
                    if stop_flag.is_set():
                        reason["value"] = reason["value"] or "stop_requested"
                    if reason["value"]:
                        docker("kill", self.editor, check=False)
                        return
                    time.sleep(0.5)

            guard = threading.Thread(target=watchdog, daemon=True)
            guard.start()
            for raw in proc.stdout:
                on_line(raw.decode("utf-8", errors="replace").rstrip("\n"))
            code = proc.wait()
            guard.join(timeout=5)
        return code, reason["value"]

    def proxy_log(self) -> bytes:
        if not self.proxy_image:
            return b""
        return docker("logs", self.proxy, check=False).stdout

    def inspect(self) -> bytes:
        names = [n for kind, n in self.created if kind == "container"]
        return docker("inspect", *names, check=False).stdout

    # ------------------------------------------------------------ teardown

    def teardown(self):
        for kind, name in reversed(self.created):
            if kind == "container":
                docker("rm", "-f", name, check=False)
            elif kind == "network":
                docker("network", "rm", name, check=False)
            elif kind == "volume":
                docker("volume", "rm", "-f", name, check=False)
        self.created.clear()
