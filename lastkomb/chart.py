from collections import defaultdict

import numpy as np
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.figure import Figure
from matplotlib.ticker import FuncFormatter, MaxNLocator

from .analysis import fmt, quantity_label

BLUE = "#2264ad"
TEAL = "#168275"
ORANGE = "#bb681d"
PURPLE = "#8657aa"


class Chart(FigureCanvasQTAgg):
    def __init__(self, on_pick, on_interval):
        self.figure = Figure(figsize=(7.5, 3.9), dpi=100, layout="constrained")
        super().__init__(self.figure)
        self.on_pick, self.on_interval = on_pick, on_interval
        self.dark = False
        self.current = None
        self.picklines = []
        self.selection_line = None
        self.start = None
        self.mpl_connect("button_press_event", self.press)
        self.mpl_connect("button_release_event", self.release)
        self.render(None, {}, {})

    def press(self, event):
        self.start = (event.x, event.xdata) if event.button == 1 and event.inaxes == self.ax else None

    def release(self, event):
        start, self.start = self.start, None
        if not start or event.xdata is None or event.inaxes != self.ax:
            return
        if abs(start[0] - event.x) > 12:
            self.on_interval(*sorted((start[1], event.xdata)))
            return
        if not self.picklines:
            return
        best = None
        for ids, ys, direction in self.picklines:
            xy = np.column_stack((self.positions[ids], ys))
            dist = np.square(self.ax.transData.transform(xy) - [event.x, event.y]).sum(axis=1)
            k = int(np.argmin(dist))
            if best is None or dist[k] < best[0]:
                best = (dist[k], ids[k], direction)
        if best and best[0] < 35**2:
            self.on_pick(int(best[1]), best[2])

    def select_point(self, idx, direction):
        if self.selection_line:
            self.selection_line.remove()
            self.selection_line = None
        if self.current is not None:
            (self.selection_line,) = self.ax.plot(
                self.positions[idx],
                self.ordinates[direction][idx],
                "o",
                markersize=6,
                color="#dd842d",
                markeredgecolor="white",
                markeredgewidth=1,
                zorder=6,
            )
        self.draw_idle()

    def render(self, result, view, config, curves=None):
        self.current = result
        self.figure.clear()
        self.selection_line = None
        self.picklines = []
        self.ax = ax = self.figure.add_subplot(111)
        surface = "#19242e" if self.dark else "#ffffff"
        ink = "#dae5ee" if self.dark else "#344657"
        grid = "#34424e" if self.dark else "#dfe6eb"
        self.figure.patch.set_facecolor(surface)
        ax.set_facecolor(surface)
        ax.grid(axis="y", color=grid, linewidth=0.7)
        ax.set_axisbelow(True)
        for edge, spine in ax.spines.items():
            spine.set_color(grid)
            spine.set_visible(edge in ["bottom", "left"])
        ax.tick_params(colors=ink, length=0, pad=6, labelsize=8)
        ax.xaxis.set_major_formatter(FuncFormatter(lambda v, _: fmt(v, 3)))
        ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: fmt(v, 3)))
        if result is None:
            ax.set_axis_off()
            ax.text(
                0.5,
                0.5,
                "Inga diagramvärden i urvalet",
                transform=ax.transAxes,
                ha="center",
                va="center",
                color=ink,
            )
            self.draw_idle()
            return
        part = result.part
        pts = [part.points[i] for i in result.ids]
        self.positions = np.array([p["pos"] for p in pts])
        ax.set_xlabel(
            {"plate": "Läge längs snittet [m]", "il": "Lastens x [m]"}.get(part.kind, "x [m]"),
            color=ink,
            fontsize=9,
        )
        ax.set_ylabel(
            f"{quantity_label(result.target, config, part.name)} [{result.unit}]", color=ink, fontsize=9
        )
        ax.yaxis.set_major_locator(MaxNLocator(nbins=6))
        groups = defaultdict(list)
        ys = {p.get("y") for p in pts}
        # Never connect different faces, hangers, or parallel result lines.
        for j, p in enumerate(pts):
            lane = p.get("y") if part.kind == "beam" and 1 < len(ys) <= 12 else None
            groups[(p.get("side"), p.get("hanger"), lane)].append(j)
        il = part.kind == "il"
        upper, lower = result.bounds["max"], result.bounds["min"]
        curves = curves or {}
        self.ordinates = {"max": upper, "min": lower, **{key: c.values for key, c in curves.items()}}
        colors = {"G": "#808a95", "PED": BLUE, "VEH": ORANGE, "COMB": PURPLE}
        seen = set()
        for group, indices in groups.items():
            ids = np.array(indices)
            x = self.positions[ids]
            # At coincident abscissae independent points can have different values.
            dots = len(x) < 2 or len(np.unique(x)) < len(x) or bool(result.warnings)

            def trace(values, label, color, style, width):
                ax.plot(
                    x,
                    values[ids],
                    color=color,
                    linewidth=width,
                    linestyle="none" if dots else style,
                    marker="." if dots else None,
                    markersize=5,
                    label=label if label not in seen else None,
                )
                seen.add(label)

            if il:
                trace(upper, "Influenslinje", TEAL, "-", 2.1)
            else:
                if not dots:
                    ax.fill_between(x, lower[ids], upper[ids], color="#4a9eb3", alpha=0.14, linewidth=0)
                trace(upper, "Max", TEAL if not self.dark else "#65d6c3", "-", 2.1)
                trace(lower, "Min", BLUE if not self.dark else "#7fb6ff", "-", 2.1)
            self.picklines.append((ids, upper[ids], "max"))
            if not il:
                self.picklines.append((ids, lower[ids], "min"))
            for key, c in curves.items():
                trace(c.values, c.name, colors[c.group], "-" if c.group == "COMB" else "--", 1.1)
                self.picklines.append((ids, c.values[ids], key))
        markers = config.get("markers", {})
        if part.kind != "plate":
            for key, name in (("supports", "Lager"), ("hangers", "Hängare"), ("vehicles", "Tung axel")):
                if key == "vehicles" and not il:
                    continue
                for value in markers.get(key, []):
                    if self.positions.min() <= value <= self.positions.max():
                        ax.axvline(
                            value,
                            ls=":",
                            lw=1,
                            color=ORANGE if key == "vehicles" else "#9cabb6",
                            label=name if name not in seen else None,
                        )
                        seen.add(name)
        ax.margins(x=0.025, y=0.14)
        ax.legend(
            loc="best",
            fontsize=8,
            framealpha=0.94,
            facecolor=surface,
            labelcolor=ink,
            edgecolor=grid,
            ncol=2 if len(seen) > 2 else 1,
        )
        self.draw_idle()

    def export(self, path):
        self.figure.savefig(path, dpi=300, bbox_inches="tight", facecolor=self.figure.get_facecolor())
