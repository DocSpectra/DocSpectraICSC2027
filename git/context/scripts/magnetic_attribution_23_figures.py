"""Sprint 23 figures from the evidence JSON and the small-case run; run inside the pinned verification image.

docker run --rm --network none --read-only --entrypoint python -e MPLCONFIGDIR=/tmp --tmpfs /tmp \
  -v <context>:/ctx:ro -v <small run>:/small:ro -v <out>:/out <image> -I \
  /ctx/scripts/magnetic_attribution_23_figures.py /ctx/evidence/<evidence> /small /out

Writes PDF and PNG figures plus the CSV data each one plots.
"""
import csv
import json
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import FancyArrowPatch  # noqa: E402

BLUE, ORANGE, AQUA = "#2a78d6", "#eb6834", "#1baf7a"
INK, MUTED, GRID, SURFACE = "#0b0b0b", "#52514e", "#e6e5e1", "#fcfcfb"
plt.rcParams.update({"font.size": 8, "axes.edgecolor": MUTED, "axes.labelcolor": INK, "xtick.color": MUTED,
                     "ytick.color": MUTED, "axes.grid": True, "grid.color": GRID, "grid.linewidth": .5,
                     "axes.spines.top": False, "axes.spines.right": False, "lines.linewidth": 2, "pdf.fonttype": 42})
POS = {"a": (0.0, 0.0), "b": (1.0, 0.0), "c": (0.5, 0.85), "d": (0.0, -1.0), "e": (1.0, -1.0)}
Q_STYLE = {0.0: (AQUA, "s", "q = 0"), 0.1: (ORANGE, "^", "q = 0.10"), 0.25: (BLUE, "o", "q = 0.25")}
FLOOR = 1e-17


def save(fig, out, name, rows, header):
    fig.savefig(out / f"{name}.pdf", bbox_inches="tight")
    fig.savefig(out / f"{name}.png", dpi=200, bbox_inches="tight")
    plt.close(fig)
    with (out / f"{name}.csv").open("w", newline="", encoding="utf-8") as h:
        w = csv.writer(h, lineterminator="\n")
        w.writerow(header)
        w.writerows(rows)


def read_csv(path):
    with Path(path).open(encoding="utf-8", newline="") as h:
        return list(csv.DictReader(h))


def small_graph(small, out):
    rec = next(json.loads(x) for x in (small / "magnetic_attribution.v1.jsonl").read_text().splitlines()
               if json.loads(x)["job_id"] == "small/five#triangle")
    block = rec["q"]["0.25"]
    fig, axes = plt.subplots(1, 2, figsize=(6.6, 3.2))
    rows = []
    vmax = 0.0
    tables = {}
    for side in ("a", "b"):
        arts = block[side]["artifacts"]
        tables[side] = (read_csv(small / "artifacts" / arts["edges_csv"]), read_csv(small / "artifacts" / arts["nodes_csv"]))
        vmax = max(vmax, max(float(r["energy"]) for r in tables[side][0]))
    for ax, side in zip(axes, ("a", "b")):
        edges, nodes = tables[side]
        # theta_ij = 2 pi q (A_ij - A_ji): positive means the arc i -> j (all small-case arcs are one-way).
        arcs = [(r["i"], r["j"]) if float(r["theta_ij"]) > 0 else (r["j"], r["i"]) for r in edges]
        energy = {frozenset((r["i"], r["j"])): float(r["energy"]) for r in edges}
        mass = {r["node"]: float(r["mass"]) for r in nodes}
        for s, t in arcs:
            e = energy[frozenset((s, t))]
            on_triangle = {s, t} <= {"a", "b", "c"}
            ax.add_patch(FancyArrowPatch(POS[s], POS[t], arrowstyle="-|>", mutation_scale=9, shrinkA=11, shrinkB=11,
                                         linewidth=0.8 + 6.0 * e / vmax, color=BLUE if on_triangle else MUTED,
                                         zorder=2))
            mx, my = (POS[s][0] + POS[t][0]) / 2, (POS[s][1] + POS[t][1]) / 2
            dx, dy = POS[t][0] - POS[s][0], POS[t][1] - POS[s][1]
            norm = (dx * dx + dy * dy) ** 0.5
            px, py = -dy / norm, dx / norm
            if px * (mx - 0.5) + py * (my + 0.1) < 0:  # push the label away from the drawing's centre
                px, py = -px, -py
            ax.annotate(f"{e:.3f}", (mx + 0.13 * px, my + 0.13 * py), ha="center", va="center", fontsize=6.5,
                        color=MUTED)
            rows.append([side, s, t, e])
        for v, (x, y) in POS.items():
            ax.scatter([x], [y], s=120 + 900 * mass[v], color=SURFACE, edgecolor=INK, linewidth=1, zorder=3)
            ax.annotate(f"{v}\n{mass[v]:.2f}", (x, y), ha="center", va="center", fontsize=6.5, color=INK, zorder=4)
        lam = block[side]["lambda1"]
        title = "A: baseline" if side == "a" else "B: triangle a-b-c reversed"
        ax.set_title(f"{title}\nλ1 = {lam:.4f}", fontsize=8, color=INK)
        ax.set_xlim(-0.45, 1.45)
        ax.set_ylim(-1.35, 1.15)
        ax.set_aspect("equal")
        ax.axis("off")
    fig.suptitle("Five-node matched pair at q = 0.25: arrow width and labels = ground-mode edge energy; "
                 "node label = mass", fontsize=8, color=INK, y=1.04)
    fig.text(0.5, 0.01, "Blue arcs: the reversed triangle. Same W, degrees, partitions and trophic incoherence on both "
             "sides. Energies sum to λ1.", ha="center", fontsize=6.5, color=MUTED)
    save(fig, out, "fig-small-five-energy", rows, ["side", "source", "target", "edge_energy_q025"])


def delta_plot(evidence, out):
    rows = [r for r in evidence["table"]]
    cases = []
    for r in rows:
        if r["case"] not in cases:
            cases.append(r["case"])
    fig, ax = plt.subplots(figsize=(6.0, 0.22 * len(cases) + 1.1))
    data = []
    for yi, case in enumerate(cases):
        for r in rows:
            if r["case"] != case:
                continue
            color, marker, label = Q_STYLE[round(r["q"], 2)]
            x = max(abs(r["delta_lambda1"]), FLOOR)
            ax.scatter([x], [yi], marker=marker, s=34, zorder=3,
                       facecolor=color if r["resolved"] else SURFACE, edgecolor=color, linewidth=1.2)
            data.append([case, r["q"], r["delta_lambda1"], r["tolerance"], r["resolved"]])
    ax.axvline(1e-8, color=MUTED, linestyle="--", linewidth=1)
    ax.annotate("resolution floor 1e-8", (1e-8, len(cases) - 0.4), xytext=(3, 0), textcoords="offset points",
                fontsize=6.5, color=MUTED)
    ax.set_xscale("log")
    ax.set_xlim(FLOOR / 3, 1)
    ax.set_yticks(range(len(cases)))
    ax.set_yticklabels(cases, fontsize=6.5)
    ax.invert_yaxis()
    ax.set_xlabel("|Δλ1| (exact zero drawn at 1e-17); filled = resolved, hollow = not resolved")
    handles = [plt.Line2D([], [], linestyle="none", marker=m, markersize=6, markerfacecolor=c, markeredgecolor=c,
                          label=lab) for c, m, lab in Q_STYLE.values()]
    ax.legend(handles=handles, fontsize=6.5, frameon=False, loc="lower center", bbox_to_anchor=(0.5, 1.0), ncol=3)
    ax.set_title("Normalized magnetic λ1 change per declared pair and q", fontsize=8, color=INK, pad=18)
    save(fig, out, "fig-delta-lambda1", data, ["case", "q", "delta_lambda1", "tolerance", "resolved"])


def main():
    evidence = json.loads(Path(sys.argv[1]).read_text())
    small, out = Path(sys.argv[2]), Path(sys.argv[3])
    small_graph(small, out)
    delta_plot(evidence, out)


if __name__ == "__main__":
    main()
