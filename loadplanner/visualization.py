"""Plotly ile 3D ve 2D yerleşim görselleri. Hesaplanan gerçek koordinatları kullanır."""

from __future__ import annotations

from typing import Optional

import plotly.graph_objects as go

from .constraints import zone_arrays
from .models import Placement, Rules, VehiclePlan

# Sabit sıralı kategorik palet (renk körlüğü için doğrulanmış 8 ton).
# 8'den fazla grup olursa fazlası gri "Diğer" olur; kimlik etiket ve
# açılır bilgi kutusuyla ayrıca gösterilir (renk tek başına taşımaz).
PALETTE = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"]
OTHER = "#9a9a96"
FRAME = "#52514e"


def color_map(plan: VehiclePlan, by: str = "kod") -> dict[str, str]:
    keys = []
    for p in sorted(plan.placements, key=lambda q: (q.item.delivery_stop, q.item.code)):
        k = _group_key(p, by)
        if k not in keys:
            keys.append(k)
    return {k: (PALETTE[i] if i < len(PALETTE) else OTHER) for i, k in enumerate(keys)}


def _group_key(p: Placement, by: str) -> str:
    return f"{p.item.delivery_stop}. durak" if by == "durak" else p.item.code


def _hover(p: Placement) -> str:
    return (f"<b>{p.uid}</b> - {p.item.name}<br>"
            f"Yükleme adımı: {p.load_step} | Boşaltma: {p.unload_step}<br>"
            f"Teslimat: {p.item.delivery_stop}. durak<br>"
            f"X {p.x:.0f}-{p.x1:.0f} | Y {p.y:.0f}-{p.y1:.0f} | Z {p.z:.0f}-{p.z1:.0f} cm<br>"
            f"Ölçü (X×Y×Z): {p.dx:.0f}×{p.dy:.0f}×{p.dz:.0f} cm<br>"
            f"Yönelim: {p.orientation}<br>Ağırlık: {p.weight:.0f} kg")


def _cuboid_mesh(b, color: str, name: str, hover: str, opacity: float, legend: bool, group: str) -> go.Mesh3d:
    x0, y0, z0, x1, y1, z1 = b
    xs = [x0, x1, x1, x0, x0, x1, x1, x0]
    ys = [y0, y0, y1, y1, y0, y0, y1, y1]
    zs = [z0, z0, z0, z0, z1, z1, z1, z1]
    i = [0, 0, 4, 4, 0, 0, 1, 1, 0, 0, 3, 3]
    j = [1, 2, 5, 6, 1, 5, 2, 6, 3, 7, 2, 6]
    k = [2, 3, 6, 7, 5, 4, 6, 5, 7, 4, 6, 7]
    return go.Mesh3d(x=xs, y=ys, z=zs, i=i, j=j, k=k, color=color, opacity=opacity, flatshading=True,
                     name=name, legendgroup=group, showlegend=legend, hovertext=hover, hoverinfo="text")


def _edges(boxes) -> tuple[list, list, list]:
    xs, ys, zs = [], [], []
    for x0, y0, z0, x1, y1, z1 in boxes:
        c = [(x0, y0, z0), (x1, y0, z0), (x1, y1, z0), (x0, y1, z0),
             (x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1)]
        for a, b in [(0, 1), (1, 2), (2, 3), (3, 0), (4, 5), (5, 6), (6, 7), (7, 4), (0, 4), (1, 5), (2, 6), (3, 7)]:
            xs += [c[a][0], c[b][0], None]
            ys += [c[a][1], c[b][1], None]
            zs += [c[a][2], c[b][2], None]
    return xs, ys, zs


def visible(plan: VehiclePlan, upto_step: Optional[int]) -> list[Placement]:
    pls = sorted(plan.placements, key=lambda p: p.load_step)
    return pls if upto_step is None else [p for p in pls if p.load_step <= upto_step]


def figure_3d(plan: VehiclePlan, rules: Rules, upto_step: Optional[int] = None, labels: bool = True,
              color_by: str = "kod", highlight: Optional[set[str]] = None) -> go.Figure:
    v = plan.vehicle
    fig = go.Figure()
    cmap = color_map(plan, color_by)
    highlight = highlight or set()

    # Kasa tel kafes
    ex, ey, ez = _edges([(0, 0, 0, v.length, v.width, v.height)])
    fig.add_trace(go.Scatter3d(x=ex, y=ey, z=ez, mode="lines", line=dict(color=FRAME, width=4),
                               name="Kasa", hoverinfo="skip"))
    # Yasak bölgeler
    zarr, _, znames = zone_arrays(v, rules)
    for b, nm in zip(zarr, znames):
        fig.add_trace(_cuboid_mesh(b, OTHER, nm, f"Yasak bölge: {nm}", 0.25, False, "zone"))

    pls = visible(plan, upto_step)
    shown_groups = set()
    for p in pls:
        g = _group_key(p, color_by)
        color = "#e34948" if p.uid in highlight else cmap.get(g, OTHER)
        legend = g not in shown_groups
        shown_groups.add(g)
        fig.add_trace(_cuboid_mesh(p.bounds(), color, g, _hover(p), 0.92, legend, g))
    if pls:
        ex, ey, ez = _edges([p.bounds() for p in pls])
        fig.add_trace(go.Scatter3d(x=ex, y=ey, z=ez, mode="lines", line=dict(color="#1a1a19", width=2),
                                   showlegend=False, hoverinfo="skip"))
    if labels and pls:
        fig.add_trace(go.Scatter3d(
            x=[p.x + p.dx / 2 for p in pls], y=[p.y + p.dy / 2 for p in pls], z=[p.z1 for p in pls],
            mode="text", text=[f"{p.load_step}:{p.item.code}" for p in pls],
            textfont=dict(size=10, color="#0b0b0b"), showlegend=False, hoverinfo="skip"))
    # Kapı işaretleri
    door_txt = []
    for d in v.doors:
        if d.kind == "arka":
            door_txt.append((v.length, v.width / 2, v.height + 10, "ARKA KAPI"))
        else:
            y = 0 if d.kind == "yan_sol" else v.width
            door_txt.append(((d.x_start + d.x_end) / 2, y, v.height + 10, "YAN KAPI"))
    door_txt.append((0, v.width / 2, v.height + 10, "ÖN (kabin)"))
    fig.add_trace(go.Scatter3d(x=[t[0] for t in door_txt], y=[t[1] for t in door_txt], z=[t[2] for t in door_txt],
                               mode="text", text=[t[3] for t in door_txt], textfont=dict(size=12, color=FRAME),
                               showlegend=False, hoverinfo="skip"))
    fig.update_layout(
        scene=dict(xaxis_title="X uzunluk (cm)", yaxis_title="Y genişlik (cm)", zaxis_title="Z yükseklik (cm)",
                   aspectmode="data", camera=dict(eye=dict(x=1.6, y=-1.6, z=1.0))),
        margin=dict(l=0, r=0, t=30, b=0), height=560, legend=dict(orientation="h", y=-0.05),
    )
    return fig


def figure_2d(plan: VehiclePlan, rules: Rules, view: str, upto_step: Optional[int] = None,
              color_by: str = "kod") -> go.Figure:
    """view: 'ust' (X-Y), 'yan' (X-Z, soldan), 'arka' (Y-Z, arka kapıdan bakış)."""
    v = plan.vehicle
    cmap = color_map(plan, color_by)
    pls = visible(plan, upto_step)
    if view == "ust":
        ax, ay, lim, order = (0, 1), ("X uzunluk (cm)", "Y genişlik (cm)"), (v.length, v.width), lambda p: p.z1
    elif view == "yan":
        ax, ay, lim, order = (0, 2), ("X uzunluk (cm)", "Z yükseklik (cm)"), (v.length, v.height), lambda p: -p.y
    else:
        ax, ay, lim, order = (1, 2), ("Y genişlik (cm)", "Z yükseklik (cm)"), (v.width, v.height), lambda p: p.x1
    fig = go.Figure()
    fig.add_shape(type="rect", x0=0, y0=0, x1=lim[0], y1=lim[1], line=dict(color=FRAME, width=2))
    zarr, _, znames = zone_arrays(v, rules)
    for b, nm in zip(zarr, znames):
        fig.add_shape(type="rect", x0=b[ax[0]], y0=b[ax[1]], x1=b[ax[0] + 3], y1=b[ax[1] + 3],
                      fillcolor=OTHER, opacity=0.35, line=dict(width=0))
    shown = set()
    for p in sorted(pls, key=order):
        b = p.bounds()
        x0, y0, x1, y1 = b[ax[0]], b[ax[1]], b[ax[0] + 3], b[ax[1] + 3]
        g = _group_key(p, color_by)
        fig.add_trace(go.Scatter(x=[x0, x1, x1, x0, x0], y=[y0, y0, y1, y1, y0], fill="toself",
                                 fillcolor=cmap.get(g, OTHER), line=dict(color="#fcfcfb", width=2), mode="lines",
                                 name=g, legendgroup=g, showlegend=g not in shown, hoveron="fills",
                                 text=_hover(p), hoverinfo="text", opacity=0.9))
        shown.add(g)
    fig.update_xaxes(title=ay[0], range=[-5, lim[0] + 5], showgrid=False, zeroline=False)
    fig.update_yaxes(title=ay[1], range=[-5, lim[1] + 5], showgrid=False, zeroline=False,
                     scaleanchor="x", scaleratio=1)
    back = "arka kapı" if any(d.kind == "arka" for d in v.doors) else "arka"
    titles = {"ust": f"Üstten görünüm (sağda {back})", "yan": f"Yandan görünüm (sol taraftan bakış, sağda {back})",
              "arka": "Arkadan görünüm (arkadan içeri bakış)"}
    fig.update_layout(title=titles[view], height=380, margin=dict(l=10, r=10, t=40, b=10),
                      legend=dict(orientation="h", y=-0.25), plot_bgcolor="#fcfcfb")
    return fig
