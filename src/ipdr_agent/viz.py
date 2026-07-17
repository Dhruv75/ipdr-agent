"""Auto-visualization: turn a result frame + viz hint into a Plotly figure.

Kept free of Streamlit so it is unit-testable and reusable outside the app.
Returns ``None`` when a chart does not make sense (the caller shows a table).
"""
from __future__ import annotations

import pandas as pd


def create_figure(data: pd.DataFrame, viz_type: str, title: str):
    if viz_type in ("none", "table") or data is None or data.empty:
        return None

    import plotly.express as px

    cols = list(data.columns)
    if not cols:
        return None

    x_col = cols[0]
    y_col = cols[1] if len(cols) > 1 else None
    is_numeric_y = y_col is not None and pd.api.types.is_numeric_dtype(data[y_col])
    title = (title[:70] + "...") if len(title) > 70 else title

    try:
        if viz_type == "bar":
            plot = data.head(20)
            if is_numeric_y:
                fig = px.bar(plot, x=x_col, y=y_col, text=y_col, title=title)
                fig.update_traces(textposition="outside")
            else:
                vc = plot[x_col].value_counts().head(20)
                fig = px.bar(x=vc.index, y=vc.values,
                             labels={"x": x_col, "y": "count"}, title=title)
            fig.update_layout(xaxis_tickangle=-45)
        elif viz_type == "line":
            plot = data.copy()
            try:
                plot[x_col] = pd.to_datetime(plot[x_col])
            except Exception:
                pass
            fig = px.line(plot, x=x_col, y=y_col if is_numeric_y else None,
                          markers=True, title=title)
        elif viz_type == "pie":
            plot = data.head(15)
            if is_numeric_y:
                fig = px.pie(plot, names=x_col, values=y_col, hole=0.3, title=title)
            else:
                vc = plot[x_col].value_counts().head(15)
                fig = px.pie(names=vc.index, values=vc.values, hole=0.3, title=title)
        else:
            return None
    except Exception:
        return None

    fig.update_layout(template="plotly_white", height=480)
    return fig
