"""Sprint 22 figures from the evidence JSON; run inside the pinned verification image (matplotlib from its lock).

docker run --rm --network none --read-only --entrypoint python -e MPLCONFIGDIR=/tmp --tmpfs /tmp \
  -v <context>:/ctx:ro -v <out>:/out <image> -I /ctx/scripts/normalized_magnetic_22_figures.py /ctx/evidence/<evidence> /out

Writes PDF and PNG figures plus the CSV data each one plots.
"""
import csv
import json
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

BLUE, ORANGE, AQUA, YELLOW = "#2a78d6", "#eb6834", "#1baf7a", "#eda100"
INK, MUTED, GRID = "#0b0b0b", "#52514e", "#e6e5e1"
plt.rcParams.update({"font.size": 8, "axes.edgecolor": MUTED, "axes.labelcolor": INK, "xtick.color": MUTED,
                     "ytick.color": MUTED, "axes.grid": True, "grid.color": GRID, "grid.linewidth": .5,
                     "axes.spines.top": False, "axes.spines.right": False, "lines.linewidth": 2, "pdf.fonttype": 42})


def save(fig, out, name, rows, header):
    fig.savefig(out / f"{name}.pdf", bbox_inches="tight")
    fig.savefig(out / f"{name}.png", dpi=200, bbox_inches="tight")
    plt.close(fig)
    with (out / f"{name}.csv").open("w", newline="", encoding="utf-8") as h:
        w = csv.writer(h, lineterminator="\n")
        w.writerow(header)
        w.writerows(rows)


def short(gid):
    return gid.split("/")[-1].replace("_docs", "").replace("_website", "")


def fig_q_response(ev, out):
    a = ev["A_characterization"]
    table = sorted(a["table"], key=lambda r: r["lwcc_nodes"])
    qs = ["0.00", "0.05", "0.10", "0.15", "0.20", "0.25"]
    fig, axes = plt.subplots(2, 7, figsize=(10, 3.4), sharex=True, sharey=True)
    rows = []
    for ax, r in zip(axes.flat, table):
        resp = a["q_response"][r["graph_id"]]
        x = [float(q) for q in qs]
        for i, (color, label) in enumerate(((BLUE, "λ1"), (ORANGE, "λ2"), (AQUA, "λ3"))):
            ys = [resp[q][i] for q in qs]
            ax.plot(x, ys, color=color, label=label)
            rows += [[r["graph_id"], q, label, y] for q, y in zip(qs, ys)]
        ax.set_title(f"{short(r['graph_id'])} (n={r['lwcc_nodes']})", fontsize=7, color=INK)
    for ax in axes[1]:
        ax.set_xlabel("q")
    for ax in axes[:, 0]:
        ax.set_ylabel("normalized eigenvalue")
    axes[0, -1].legend(frameon=False, fontsize=7, loc="upper right")
    fig.suptitle("Lowest normalized magnetic eigenvalues vs charge, historical LWCC of each corpus", fontsize=9, color=INK)
    save(fig, out, "fig-a-q-response", rows, ["graph_id", "q", "mode", "value"])


def fig_old_new(ev, out):
    a = ev["A_characterization"]
    fig, ax = plt.subplots(figsize=(3.6, 3.2))
    rows = []
    for r in a["table"]:
        x, y = r["unnorm_lambda1_q0.25"], r["lambda1_q0.25"]
        ax.scatter(x, y, s=28, color=BLUE, edgecolor="white", linewidth=1, zorder=3)
        ax.annotate(short(r["graph_id"]), (x, y), xytext=(3, 2), textcoords="offset points", fontsize=6, color=MUTED)
        rows.append([r["graph_id"], x, y, r["mean_weighted_degree"]])
    ax.set_xscale("log")
    ax.set_yscale("log")
    ax.set_xlabel("unnormalized λ1 (q = 0.25)")
    ax.set_ylabel("normalized λ1 (q = 0.25)")
    ax.set_title(f"Same graphs, old vs new operator (Spearman {a['old_vs_new']['rank_spearman_lambda1']:.2f})",
                 fontsize=8, color=INK)
    save(fig, out, "fig-a-old-vs-new", rows, ["graph_id", "unnormalized_lambda1_q0.25", "normalized_lambda1_q0.25",
                                               "mean_weighted_degree"])


def fig_orientation(ev, out):
    b = ev["B_orientation"]["families"]
    fig, axes = plt.subplots(1, 2, figsize=(7, 3), sharey=True)
    rows = []
    styles = {"random_half": (BLUE, "o", "random half"), "partial": (AQUA, "s", "partial"),
              "as_written": (INK, "D", "F (as written)"), "all_one_way_reversed": (ORANGE, "^", "R (reversed)"),
              "two_way": (YELLOW, "v", "U (two-way)")}
    for ax, tag in zip(axes, ("main", "sensitivity")):
        seen = set()
        for r in b[tag]["table"]:
            fam = "partial" if r["family"].startswith("partial_") else r["family"]
            color, marker, label = styles[fam]
            ax.scatter(r["lambda1_q0.25"], r["expansion_added_rate"], s=30, color=color, marker=marker,
                       edgecolor="white", linewidth=1, label=None if fam in seen else label, zorder=3)
            seen.add(fam)
            rows.append([tag, r["version_id"], r["family"], r["lambda1_q0.25"], r["expansion_added_rate"]])
        t = b[tag]["tests_normalized"]["primary"]["lambda1_q0.25"]
        ax.set_title(f"{tag}: ρ = {t['rho']:.2f}, Holm p = {t['holm_p']:.3f} (29 versions)", fontsize=8, color=INK)
        ax.set_xlabel("normalized λ1 (q = 0.25), fixed LWCC")
    axes[0].set_ylabel("expansion-added rate\n(automatic candidate support)")
    axes[1].legend(frameon=False, fontsize=6, loc="best")
    fig.suptitle("Orientation variants: exploratory replay of the 09c exposure table", fontsize=9, color=INK)
    save(fig, out, "fig-b-orientation", rows, ["edge_set", "version_id", "family", "normalized_lambda1_q0.25",
                                                "expansion_added_rate"])


def fig_refinement(ev, out):
    c = ev["C_refinement"]["profiles"]
    measures = (("lambda1_q0.25", "normalized λ1 (q = 0.25)"), ("lambda2_q0.25", "normalized λ2 (q = 0.25)"),
                ("weighted_q0_lambda2", "q = 0 λ2 (same W)"))
    fig, axes = plt.subplots(1, 3, figsize=(9, 2.8))
    rows = []
    for ax, (key, label) in zip(axes, measures):
        for xi, arm in enumerate(("LT", "R0")):
            for off, (t, color) in enumerate((("ln", BLUE), ("pc", AQUA), ("rn", YELLOW))):
                ys = [c[f"wc/{arm}/{t}-{k}"][key] for k in range(1, 6)]
                ax.scatter([xi * 5 + off + 1] * len(ys), ys, s=14, color=color, label=t.upper() if xi == 0 else None,
                           zorder=3)
                rows += [[key, arm, t.upper(), k, y] for k, y in enumerate(ys, 1)]
            ax.scatter([xi * 5 + 4], [c[f"wc/{arm}"][key]], s=40, color=ORANGE, marker="D", label="arm" if xi == 0 else None,
                       edgecolor="white", linewidth=1, zorder=4)
            ax.scatter([xi * 5], [c[f"wc/{arm}/O-prime"][key]], s=40, color=INK, marker="s",
                       label="O′" if xi == 0 else None, zorder=4)
            rows += [[key, arm, "arm", "", c[f"wc/{arm}"][key]], [key, arm, "O-prime", "", c[f"wc/{arm}/O-prime"][key]]]
        ax.axhline(c["wc/O"][key], color=MUTED, linewidth=1, linestyle="--")
        ax.set_xticks([2, 7])
        ax.set_xticklabels(["LT", "R0"])
        ax.set_title(label, fontsize=8, color=INK)
    axes[0].legend(frameon=False, fontsize=6, ncol=2)
    fig.suptitle("Whole-corpus LWCC profiles: each arm against its own null families (dashed: O)", fontsize=9, color=INK)
    save(fig, out, "fig-c-refinement", rows, ["measure", "arm", "graph", "seed_index", "value"])


def main():
    ev = json.loads(Path(sys.argv[1]).read_bytes())
    out = Path(sys.argv[2])
    for fn in (fig_q_response, fig_old_new, fig_orientation, fig_refinement):
        fn(ev, out)
    print("figures written")


if __name__ == "__main__":
    main()
