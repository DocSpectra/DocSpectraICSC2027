from __future__ import annotations

"""Graph artifact and metric generation for CIKM corpus views."""

import argparse
import csv
import json
import re
from collections import Counter, deque
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from .retrieval_arena_export import Page, discover_markdown_pages, extract_markdown_edges


try:  # pragma: no cover - availability differs by runtime
    import numpy as np
except ModuleNotFoundError:  # pragma: no cover
    np = None  # type: ignore[assignment]


@dataclass(frozen=True)
class GraphArtifactsConfig:
    corpus_name: str
    docs_root: Path
    output_root: Path
    view: str
    faq_patterns: tuple[str, ...] = ("*faq*",)


DEFAULT_MAGNETIC_Q = 0.25
DEFAULT_DISPERSION_K = 10
DEFAULT_PHASE_K = 10


def title_from_markdown(text: str, fallback: str) -> str:
    for line in text.splitlines():
        match = re.match(r"^#\s+(.+?)\s*$", line)
        if match:
            return " ".join(match.group(1).split())
    return fallback


def token_count(text: str) -> int:
    return len(re.findall(r"[A-Za-z0-9_]+", text))


def section_count(text: str) -> int:
    return sum(1 for line in text.splitlines() if re.match(r"^#{1,6}\s+", line))


def undirected_adjacency(nodes: list[str], edges: Iterable[tuple[str, str]]) -> dict[str, set[str]]:
    adjacency = {node: set() for node in nodes}
    for source, target in edges:
        if source == target:
            continue
        if source in adjacency and target in adjacency:
            adjacency[source].add(target)
            adjacency[target].add(source)
    return adjacency


def weak_components(nodes: list[str], edges: Iterable[tuple[str, str]]) -> list[set[str]]:
    adjacency = undirected_adjacency(nodes, edges)
    seen: set[str] = set()
    components: list[set[str]] = []
    for node in nodes:
        if node in seen:
            continue
        component: set[str] = set()
        queue = deque([node])
        seen.add(node)
        while queue:
            current = queue.popleft()
            component.add(current)
            for neighbor in adjacency[current]:
                if neighbor not in seen:
                    seen.add(neighbor)
                    queue.append(neighbor)
        components.append(component)
    components.sort(key=lambda item: (-len(item), sorted(item)[0] if item else ""))
    return components


def largest_weak_component(nodes: list[str], edges: list[tuple[str, str]]) -> set[str]:
    components = weak_components(nodes, edges)
    return components[0] if components else set()


def eigenvalue_distribution(eigenvalues: "np.ndarray") -> dict:
    eigen_list = [float(value) for value in eigenvalues.tolist()]
    return {
        "min": float(eigenvalues[0]) if eigenvalues.size else 0.0,
        "max": float(eigenvalues[-1]) if eigenvalues.size else 0.0,
        "first_10": eigen_list[:10],
        "last_10": eigen_list[-10:],
    }


def spectral_spacing_metrics(eigenvalues: "np.ndarray") -> dict:
    gaps = np.diff(eigenvalues) if eigenvalues.size > 1 else np.array([], dtype=float)
    spectral_gap = float(gaps[0]) if gaps.size else 0.0
    mean_gap = float(np.mean(gaps)) if gaps.size else 0.0
    std_gap = float(np.std(gaps)) if gaps.size else 0.0
    return {
        "spectral_gap": spectral_gap,
        "mean_spectral_gap": mean_gap,
        "std_spectral_gap": std_gap,
        "gap_ratio": float(spectral_gap / mean_gap) if mean_gap > 0.0 else 0.0,
        "gaps_nonnegative": bool(np.all(gaps >= -1e-10)) if gaps.size else True,
        "min_eigenvalue_gap": float(np.min(gaps)) if gaps.size else 0.0,
    }


def undirected_laplacian_metrics(nodes: list[str], edges: list[tuple[str, str]]) -> dict:
    if np is None:
        return {
            "status": "unavailable",
            "method": "numpy_eigvalsh_undirected_laplacian",
            "reason": "numpy is not installed in this Python runtime",
        }
    n = len(nodes)
    if n == 0:
        return {"status": "computed", "method": "numpy_eigvalsh_undirected_laplacian", "eigenvalues": [], "lambda_2": 0.0, "spectral_gap": 0.0}
    index = {node: i for i, node in enumerate(nodes)}
    adjacency = np.zeros((n, n), dtype=float)
    for source, target in edges:
        if source == target or source not in index or target not in index:
            continue
        i = index[source]
        j = index[target]
        adjacency[i, j] = 1.0
        adjacency[j, i] = 1.0
    degrees = np.sum(adjacency, axis=1)
    laplacian = np.diag(degrees) - adjacency
    eigenvalues = np.linalg.eigvalsh(laplacian)
    eigenvalues[np.abs(eigenvalues) < 1e-10] = 0.0
    eigenvalues = np.sort(eigenvalues)
    spacing = spectral_spacing_metrics(eigenvalues)
    zero_multiplicity = int(np.sum(np.abs(eigenvalues) <= 1e-10))
    expected_zero_multiplicity = len(weak_components(nodes, edges))
    lambda_2 = float(eigenvalues[1]) if eigenvalues.size > 1 else 0.0
    max_eigenvalue = float(eigenvalues[-1]) if eigenvalues.size else 0.0
    return {
        "status": "computed",
        "method": "numpy_eigvalsh_undirected_laplacian",
        "lambda_2": lambda_2,
        "lambda_2_norm": float(lambda_2 / max_eigenvalue) if max_eigenvalue > 0.0 else 0.0,
        "min_eigenvalue": float(eigenvalues[0]) if eigenvalues.size else 0.0,
        "max_eigenvalue": max_eigenvalue,
        "zero_eigenvalue_multiplicity": zero_multiplicity,
        "expected_zero_multiplicity": expected_zero_multiplicity,
        "zero_behavior_ok": bool(zero_multiplicity == expected_zero_multiplicity),
        "nonnegative_within_tolerance": bool(eigenvalues[0] >= -1e-10) if eigenvalues.size else True,
        **spacing,
        "eigenvalue_count": int(eigenvalues.size),
        "eigenvalue_distribution": eigenvalue_distribution(eigenvalues),
    }


def normalize_phases(eigenvector: "np.ndarray") -> "np.ndarray":
    anchor = np.angle(np.sum(eigenvector))
    rotated = eigenvector * np.exp(-1j * anchor)
    return np.angle(rotated)


def magnetic_phase_metrics(nodes: list[str], edges: list[tuple[str, str]], eigenvectors: "np.ndarray", *, phase_k: int) -> dict:
    n = len(nodes)
    if n == 0 or eigenvectors.shape[1] <= 1:
        return {"phase_metrics_available": False, "phase_var": 0.0, "flow_imbalance": 0.0}
    phase_upper = min(max(2, phase_k + 1), int(eigenvectors.shape[1]))
    phase_indices = list(range(1, phase_upper))
    if not phase_indices:
        return {"phase_metrics_available": False, "phase_var": 0.0, "flow_imbalance": 0.0}

    phase_arrays = [normalize_phases(eigenvectors[:, index]) for index in phase_indices]
    phase_all = np.concatenate(phase_arrays)
    phase_var = float(np.var(phase_all))

    node_phase = np.zeros(n, dtype=float)
    for index in phase_indices:
        node_phase += normalize_phases(eigenvectors[:, index])
    node_phase /= max(1, len(phase_indices))

    node_index = {node: i for i, node in enumerate(nodes)}
    forward = 0
    backward = 0
    for source, target in edges:
        if source not in node_index or target not in node_index:
            continue
        delta = np.angle(np.exp(1j * (node_phase[node_index[target]] - node_phase[node_index[source]])))
        if delta >= 0:
            forward += 1
        else:
            backward += 1
    total = forward + backward
    return {
        "phase_metrics_available": True,
        "phase_var": phase_var,
        "flow_imbalance": float(abs(forward - backward) / total) if total else 0.0,
    }


def magnetic_laplacian_metrics(nodes: list[str], edges: list[tuple[str, str]], *, q: float = DEFAULT_MAGNETIC_Q, dispersion_k: int = DEFAULT_DISPERSION_K, phase_k: int = DEFAULT_PHASE_K) -> dict:
    if np is None:
        return {
            "status": "unavailable",
            "method": "numpy_eigh_magnetic_laplacian",
            "reason": "numpy is not installed in this Python runtime",
        }
    n = len(nodes)
    if n == 0:
        return {
            "status": "computed",
            "method": "numpy_eigh_magnetic_laplacian",
            "q": float(q),
            "eigenvalue_count": 0,
            "eigenvalue_distribution": {"min": 0.0, "max": 0.0, "first_10": [], "last_10": []},
            "lambda_2_magnetic": 0.0,
            "lambda2_mag": 0.0,
            "dispersion_k": 0.0,
            "phase_var": 0.0,
            "flow_imbalance": 0.0,
            "spectral_gap": 0.0,
            "mean_spectral_gap": 0.0,
            "std_spectral_gap": 0.0,
            "gap_ratio": 0.0,
            "gaps_nonnegative": True,
            "min_eigenvalue_gap": 0.0,
            "hermitian_fro_error": 0.0,
            "hermitian_within_tolerance": True,
            "max_abs_imag_eigenvalue": 0.0,
            "real_eigenvalues_within_tolerance": True,
            "phase_metrics_available": False,
            "config": {"q": float(q), "dispersion_k": int(dispersion_k), "phase_k": int(phase_k), "weight_mode": "binary", "normalize": "none"},
        }
    index = {node: i for i, node in enumerate(nodes)}
    adjacency = np.zeros((n, n), dtype=float)
    for source, target in edges:
        if source == target or source not in index or target not in index:
            continue
        adjacency[index[source], index[target]] = 1.0

    weights = 0.5 * (adjacency + adjacency.T)
    flow = adjacency - adjacency.T
    theta = 2.0 * np.pi * q * flow
    hermitian_adjacency = weights * np.exp(1j * theta)
    degree = np.diag(weights.sum(axis=1)).astype(np.complex128)
    laplacian = degree - hermitian_adjacency

    hermitian_fro_error = float(np.linalg.norm(laplacian - laplacian.conj().T, ord="fro"))
    eigenvalues, eigenvectors = np.linalg.eigh(laplacian)
    eigenvalues[np.abs(eigenvalues) < 1e-10] = 0.0
    eigenvalues = np.sort(eigenvalues.astype(float))
    general_eigenvalues = np.linalg.eigvals(laplacian)
    max_abs_imag = float(np.max(np.abs(np.imag(general_eigenvalues)))) if general_eigenvalues.size else 0.0
    spacing = spectral_spacing_metrics(eigenvalues)
    upper = min(max(2, dispersion_k + 1), int(eigenvalues.size))
    dispersion_slice = eigenvalues[1:upper] if eigenvalues.size > 1 else np.array([0.0], dtype=float)
    dispersion = float(np.std(dispersion_slice)) if dispersion_slice.size else 0.0
    phase = magnetic_phase_metrics(nodes, edges, eigenvectors, phase_k=phase_k)
    lambda2_mag = float(abs(eigenvalues[1])) if eigenvalues.size > 1 else 0.0
    return {
        "status": "computed",
        "method": "numpy_eigh_magnetic_laplacian",
        "q": float(q),
        "lambda_2_magnetic": lambda2_mag,
        "lambda2_mag": lambda2_mag,
        "dispersion_k": dispersion,
        "phase_var": phase["phase_var"],
        "flow_imbalance": phase["flow_imbalance"],
        **spacing,
        "eigenvalue_count": int(eigenvalues.size),
        "eigenvalue_distribution": eigenvalue_distribution(eigenvalues),
        "hermitian_fro_error": hermitian_fro_error,
        "hermitian_within_tolerance": bool(hermitian_fro_error <= 1e-8),
        "max_abs_imag_eigenvalue": max_abs_imag,
        "real_eigenvalues_within_tolerance": bool(max_abs_imag <= 1e-8),
        "phase_metrics_available": phase["phase_metrics_available"],
        "config": {"q": float(q), "dispersion_k": int(dispersion_k), "phase_k": int(phase_k), "weight_mode": "binary", "normalize": "none"},
    }


def directed_spectral_summary(magnetic: dict) -> dict:
    return {
        "status": magnetic.get("status"),
        "method": "magnetic_laplacian",
        "q": magnetic.get("q"),
        "lambda_2_magnetic": magnetic.get("lambda_2_magnetic"),
        "eigenvalue_count": magnetic.get("eigenvalue_count"),
        "hermitian_within_tolerance": magnetic.get("hermitian_within_tolerance"),
        "real_eigenvalues_within_tolerance": magnetic.get("real_eigenvalues_within_tolerance"),
    }


def structural_metrics(nodes: list[str], edges: list[tuple[str, str]]) -> dict:
    node_count = len(nodes)
    edge_count = len(edges)
    components = weak_components(nodes, edges)
    largest_size = len(components[0]) if components else 0
    out_degree = Counter(source for source, _ in edges)
    in_degree = Counter(target for _, target in edges)
    total_degrees = [out_degree[node] + in_degree[node] for node in nodes]
    density = edge_count / (node_count * (node_count - 1)) if node_count > 1 else 0.0
    return {
        "number_of_nodes": node_count,
        "number_of_edges": edge_count,
        "number_of_weak_components": len(components),
        "largest_weak_component_size": largest_size,
        "largest_weak_component_fraction": largest_size / node_count if node_count else 0.0,
        "mean_degree": sum(total_degrees) / node_count if node_count else 0.0,
        "max_degree": max(total_degrees) if total_degrees else 0,
        "density": density,
    }


def write_nodes(path: Path, pages: list[Page]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["node_id", "rel_path", "page_title", "token_count", "section_count", "is_faq"])
        writer.writeheader()
        for page in pages:
            writer.writerow(
                {
                    "node_id": page.doc_id,
                    "rel_path": page.rel_path,
                    "page_title": title_from_markdown(page.text, page.rel_path),
                    "token_count": token_count(page.text),
                    "section_count": section_count(page.text),
                    "is_faq": str(page.is_faq).lower(),
                }
            )


def write_edges(path: Path, edges: list[tuple[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["source", "target"])
        writer.writerows(edges)


def write_metrics(path: Path, corpus_name: str, view: str, nodes: list[str], edges: list[tuple[str, str]], *, source_view: str | None = None) -> dict:
    undirected = undirected_laplacian_metrics(nodes, edges)
    magnetic = magnetic_laplacian_metrics(nodes, edges)
    metrics = {
        "corpus_name": corpus_name,
        "view": view,
        "source_view": source_view,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "structural": structural_metrics(nodes, edges),
        "spectral": {
            "undirected_laplacian": undirected,
            "magnetic_laplacian": magnetic,
            "directed": directed_spectral_summary(magnetic),
        },
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(metrics, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return metrics


def write_view(output_dir: Path, corpus_name: str, view: str, pages: list[Page], edges: list[tuple[str, str]], *, source_view: str | None = None) -> dict:
    nodes = [page.doc_id for page in pages]
    write_nodes(output_dir / "nodes.csv", pages)
    write_edges(output_dir / "graph_edges.csv", edges)
    return write_metrics(output_dir / "graph_metrics.json", corpus_name, view, nodes, edges, source_view=source_view)


def build_graph_artifacts(config: GraphArtifactsConfig) -> dict:
    docs_root = config.docs_root.resolve()
    pages = discover_markdown_pages(docs_root, faq_patterns=list(config.faq_patterns))
    if config.view == "full":
        view_pages = pages
    elif config.view == "retrieval_no_faq":
        view_pages = [page for page in pages if not page.is_faq]
    else:
        raise ValueError(f"Unsupported graph view: {config.view}")
    edges = extract_markdown_edges(view_pages)
    view_root = config.output_root / config.corpus_name / config.view
    metrics = write_view(view_root, config.corpus_name, config.view, view_pages, edges)

    lwcc_nodes = largest_weak_component([page.doc_id for page in view_pages], edges)
    lwcc_pages = [page for page in view_pages if page.doc_id in lwcc_nodes]
    lwcc_edges = [(source, target) for source, target in edges if source in lwcc_nodes and target in lwcc_nodes]
    lwcc_view = f"{config.view}_lwcc" if config.view != "full" else "full_lwcc"
    lwcc_root = config.output_root / config.corpus_name / lwcc_view
    lwcc_metrics = write_view(lwcc_root, config.corpus_name, lwcc_view, lwcc_pages, lwcc_edges, source_view=config.view)
    return {"view": metrics, "lwcc": lwcc_metrics}


def parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build graph and spectral metric artifacts for one corpus view")
    parser.add_argument("--corpus-name", required=True)
    parser.add_argument("--docs-root", required=True)
    parser.add_argument("--output-root", default="graphs")
    parser.add_argument("--view", choices=["full", "retrieval_no_faq"], required=True)
    parser.add_argument("--faq-pattern", action="append", default=[])
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    result = build_graph_artifacts(
        GraphArtifactsConfig(
            corpus_name=args.corpus_name,
            docs_root=Path(args.docs_root),
            output_root=Path(args.output_root),
            view=args.view,
            faq_patterns=tuple(args.faq_pattern) if args.faq_pattern else ("*faq*",),
        )
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
