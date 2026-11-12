"""Dashboard pages: HTML with Plotly charts, shown in the built-in browser.

plotly.js is loaded from the local file the plotly package ships, so nothing comes from the
internet. The page is written to a file and loaded, not passed to setHtml(): setHtml() caps
content at 2 MB and plotly.js alone is about 4.6 MB.
"""

import html
import importlib.util
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from comment_analyzer.analysis.language import language_name
from comment_analyzer.analysis.summarize import SENTIMENTS, mood, summary_lines
from comment_analyzer.platforms.youtube import DISLIKES_KEY
from comment_analyzer.reports.data import CATEGORIES, COLORS, PostReport, growth

Figure = dict[str, Any]


@dataclass(frozen=True, slots=True)
class Theme:
    text: str = "#1b1b1b"
    muted: str = "#6b6a66"
    background: str = "#ffffff"
    border: str = "#d8d7d2"


def plotly_js() -> Path:
    """The plotly.min.js file: inside the plotly package, or next to the built app."""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", "")) / "plotly" / "package_data" / "plotly.min.js"
    spec = importlib.util.find_spec("plotly")  # found without importing the Python package
    assert spec is not None and spec.submodule_search_locations
    return Path(spec.submodule_search_locations[0]) / "package_data" / "plotly.min.js"


def empty_page(message: str, theme: Theme) -> str:
    return _page(f'<p class="note">{html.escape(message)}</p>', {}, theme, "")


def post_page(report: PostReport, theme: Theme, plotly_url: str) -> str:
    analysis = report.analysis
    snapshot = report.snapshot
    tiles = []
    if snapshot is not None:
        for label, value in (
            ("Views", snapshot.views),
            ("Likes", snapshot.likes),
            ("Comments (reported)", snapshot.comments_reported),
        ):
            tiles.append((label, f"{value:,}" if value is not None else "not public"))
        estimate = snapshot.extra.get(DISLIKES_KEY)
        if isinstance(estimate, int):
            tiles.insert(2, ("Dislikes (estimate)", f"{estimate:,}"))
    tiles.append(("Collected", f"{len(report.comments):,}"))
    if analysis is not None and analysis.counted:
        tiles.append(("Mood", mood(analysis.shares)))
        tiles.append(("Negative", f"{analysis.shares['negative']:.0f}%"))
    body = [
        f"<h1>{html.escape(report.title)}</h1>",
        f'<p class="note">{html.escape(report.coverage())}</p>',
        '<div class="tiles">'
        + "".join(
            f"<div class=tile><span>{html.escape(k)}</span><b>{html.escape(v)}</b></div>"
            for k, v in tiles
        )
        + "</div>",
    ]
    figures: dict[str, Figure] = {}
    if analysis is not None:
        lines = summary_lines(analysis)[:-1]
        body.append("<ul>" + "".join(f"<li>{html.escape(line)}</li>" for line in lines) + "</ul>")
        figures["sentiment"] = _sentiment_figure(report, theme)
    else:
        body.append(
            '<p class="note">Not analysed yet: use "Analyse comments" on the Analyse page.</p>'
        )
    timeline = _timeline_figure(report, theme)
    if timeline is not None:
        figures["timeline"] = timeline
    if len(report.snapshots) >= 2:
        figures["growth"] = _growth_figure(report, theme)
    if analysis is not None and analysis.topics:
        figures["topics"] = _topics_figure(report, theme)
    if analysis is not None and analysis.issues:
        figures["issues"] = _issues_figure(report, theme)
    if analysis is not None:
        figures["languages"] = _languages_figure(report, theme)
    body += [f'<div id="{name}" class="chart"></div>' for name in figures]
    body.append(f'<p class="note">{html.escape(report.footer())}</p>')
    return _page("\n".join(body), figures, theme, plotly_url)


def compare_page(reports: list[PostReport], theme: Theme, plotly_url: str) -> str:
    rows = []
    for r in reports:
        a = r.analysis
        mood_text = (
            f"{mood(a.shares)} ({a.shares['positive']:.0f}% / {a.shares['negative']:.0f}%)"
            if a and a.counted
            else "not analysed"
        )
        rows.append(
            f"<tr><td>{html.escape(_short(r.title, 60))}</td><td>{len(r.comments):,}</td>"
            f"<td>{html.escape(mood_text)}</td></tr>"
        )
    table = (
        "<table><tr><th>Post</th><th>Collected</th><th>Mood (positive / negative)</th></tr>"
        + "".join(rows)
        + "</table>"
    )
    shares: Figure = {
        "data": [
            {
                "type": "bar",
                "name": _short(r.title, 30),
                "x": list(SENTIMENTS),
                "y": [round(r.analysis.shares[s], 1) if r.analysis else 0 for s in SENTIMENTS],
                "hovertemplate": "%{x}: %{y}%<extra>%{fullData.name}</extra>",
            }
            for r in reports
        ],
        "layout": _layout(theme, "Sentiment shares (spam left out)", yaxis={"ticksuffix": "%"}),
    }
    lines: Figure = {
        "data": [
            {
                "type": "scatter",
                "mode": "lines",
                "name": _short(r.title, 30),
                "x": [round(h, 2) for h, _ in growth(r)],
                "y": [n for _, n in growth(r)],
            }
            for r in reports
        ],
        "layout": _layout(
            theme,
            "Comments collected, by hours since posting",
            xaxis={"title": {"text": "Hours since posting"}},
            yaxis={"title": {"text": "Comments"}},
        ),
    }
    note = (
        "Each post's coverage and collection time differ, so compare shares rather than counts. "
        "Comments are not a poll."
    )
    body = f'<h1>Compare posts</h1>{table}<div id="shares" class="chart"></div>'
    body += f'<div id="growth" class="chart"></div><p class="note">{html.escape(note)}</p>'
    return _page(body, {"shares": shares, "growth": lines}, theme, plotly_url)


def _sentiment_figure(report: PostReport, theme: Theme) -> Figure:
    analysis = report.analysis
    assert analysis is not None
    rows = ["Weighted by likes", "Comments"]
    data = [
        {
            "type": "bar",
            "orientation": "h",
            "name": s,
            "y": rows,
            "x": [round(analysis.weighted[s], 1), round(analysis.shares[s], 1)],
            "marker": {"color": COLORS[s]},
            "text": [f"{analysis.weighted[s]:.0f}%", f"{analysis.shares[s]:.0f}%"],
            "textposition": "inside",
            "hovertemplate": "%{y}: %{x}%<extra>" + s + "</extra>",
        }
        for s in SENTIMENTS
    ]
    layout = _layout(
        theme,
        f"Sentiment ({analysis.counted:,} comments, spam left out)",
        barmode="stack",
        height=230,
        xaxis={"range": [0, 100], "ticksuffix": "%"},
    )
    return {"data": data, "layout": layout}


def _timeline_figure(report: PostReport, theme: Theme) -> Figure | None:
    timeline = report.timeline()
    if timeline is None:
        return None
    x = [start.isoformat() for start in timeline.starts]
    data = [
        {"type": "bar", "name": c, "x": x, "y": timeline.counts[c], "marker": {"color": COLORS[c]}}
        for c in CATEGORIES
        if any(timeline.counts[c])
    ]
    layout = _layout(
        theme,
        f"When people commented (per {timeline.unit})",
        barmode="stack",
        yaxis={"title": {"text": "Comments"}},
    )
    published = report.post.published_at
    if published is not None:
        when = published.astimezone().replace(tzinfo=None).isoformat()
        layout["shapes"] = [
            {
                "type": "line",
                "x0": when,
                "x1": when,
                "yref": "paper",
                "y0": 0,
                "y1": 1,
                "line": {"dash": "dash", "color": theme.muted, "width": 1},
            }
        ]
    return {"data": data, "layout": layout}


def _growth_figure(report: PostReport, theme: Theme) -> Figure:
    """Views and reported comments at each tracked check (the snapshots)."""
    x = [s.taken_at.astimezone().replace(tzinfo=None).isoformat() for s in report.snapshots]
    data = [
        {"type": "scatter", "mode": "lines+markers", "name": "views", "x": x,
         "y": [s.views for s in report.snapshots], "line": {"color": COLORS["positive"]}},
        {"type": "scatter", "mode": "lines+markers", "name": "comments (reported)", "x": x,
         "y": [s.comments_reported for s in report.snapshots], "yaxis": "y2",
         "line": {"color": COLORS["negative"]}},
    ]  # fmt: skip
    layout = _layout(theme, "Growth at each tracked check")
    layout["yaxis2"] = {"overlaying": "y", "side": "right", "showgrid": False}
    return {"data": data, "layout": layout}


def _topics_figure(report: PostReport, theme: Theme) -> Figure:
    """One bar per topic, split by sentiment; the hover shows its most-liked comment."""
    topics = list(reversed(report.analysis.topics if report.analysis else []))
    labels = [_short(t.label, 40) for t in topics]
    examples = [html.escape(_short(" ".join((t.example or "").split()), 120)) for t in topics]
    data = [
        {
            "type": "bar",
            "orientation": "h",
            "name": s,
            "y": labels,
            "x": [round(t.comments * t.sentiments[s] / 100) for t in topics],
            "customdata": examples,
            "marker": {"color": COLORS[s]},
            "hovertemplate": "%{y}: %{x} " + s + "<br><i>%{customdata}</i><extra></extra>",
        }
        for s in SENTIMENTS
    ]
    height = max(240, 60 + 30 * len(topics))
    layout = _layout(
        theme,
        "What comments talk about (groups of comments that say much the same thing)",
        barmode="stack",
        height=height,
        xaxis={"title": {"text": "Comments"}},
    )
    layout["margin"]["l"] = 200
    return {"data": data, "layout": layout}


def _issues_figure(report: PostReport, theme: Theme) -> Figure:
    issues = list(reversed(report.analysis.issues if report.analysis else []))
    data = [
        {
            "type": "bar",
            "orientation": "h",
            "y": [i.term for i in issues],
            "x": [i.mentions for i in issues],
            "marker": {"color": COLORS["negative"]},
            "hovertemplate": "%{y}: %{x} negative comments<extra></extra>",
        }
    ]
    height = max(220, 40 + 28 * len(issues))
    layout = _layout(theme, "What negative comments mention most", height=height, showlegend=False)
    return {"data": data, "layout": layout}


def _languages_figure(report: PostReport, theme: Theme) -> Figure:
    analysis = report.analysis
    assert analysis is not None
    top = analysis.languages[:6]
    data = [
        {
            "type": "bar",
            "x": [language_name(code) for code, _ in top],
            "y": [n for _, n in top],
            "marker": {"color": COLORS["neutral"]},
            "hovertemplate": "%{x}: %{y} comments<extra></extra>",
        }
    ]
    return {"data": data, "layout": _layout(theme, "Languages", showlegend=False, height=260)}


def _layout(theme: Theme, title: str, **extra: Any) -> dict[str, Any]:
    layout: dict[str, Any] = {
        "title": {"text": title, "x": 0, "font": {"size": 14}},
        "paper_bgcolor": "rgba(0,0,0,0)",
        "plot_bgcolor": "rgba(0,0,0,0)",
        "font": {"family": "Segoe UI, Malgun Gothic, sans-serif", "color": theme.text},
        "margin": {"l": 60, "r": 20, "t": 40, "b": 40},
        "legend": {"orientation": "h", "y": -0.2},
        "height": 320,
    }
    for axis in ("xaxis", "yaxis"):
        layout[axis] = {"gridcolor": theme.border, "zerolinecolor": theme.border}
    for key, value in extra.items():
        if key in ("xaxis", "yaxis"):
            layout[key].update(value)
        else:
            layout[key] = value
    return layout


def _page(body: str, figures: dict[str, Figure], theme: Theme, plotly_url: str) -> str:
    # "</" inside a <script> would end it early
    data = json.dumps(figures, ensure_ascii=False).replace("</", "<\\/")
    script = f'<script src="{html.escape(plotly_url)}"></script>' if plotly_url else ""
    return f"""<!doctype html>
<html><head><meta charset="utf-8">{script}
<style>
body {{ font: 14px "Segoe UI", "Malgun Gothic", sans-serif; margin: 16px; color: {theme.text};
       background: {theme.background}; }}
h1 {{ font-size: 18px; margin: 0 0 4px; }}
.note {{ color: {theme.muted}; }}
.tiles {{ display: flex; flex-wrap: wrap; gap: 10px; margin: 12px 0; }}
.tile {{ border: 1px solid {theme.border}; border-radius: 8px; padding: 8px 14px;
         min-width: 110px; }}
.tile span {{ display: block; color: {theme.muted}; font-size: 12px; }}
.tile b {{ font-size: 20px; }}
.chart {{ margin: 10px 0; }}
table {{ border-collapse: collapse; margin: 10px 0; }}
td, th {{ border-bottom: 1px solid {theme.border}; padding: 4px 10px; text-align: left; }}
</style></head>
<body>
{body}
<script>
const figures = {data};
for (const [id, figure] of Object.entries(figures)) {{
  Plotly.newPlot(id, figure.data, figure.layout, {{displaylogo: false, responsive: true}});
}}
</script>
</body></html>
"""


def _short(text: str, length: int) -> str:
    return text if len(text) <= length else text[: length - 1] + "…"
