from collections import defaultdict

from plotly.offline import plot
from plotly.offline.offline import get_plotlyjs
from plotly.graph_objs import Scatter, Figure, Layout

STATUS_ORDER = (
    'dropped',
    'py3-only',
    'legacy-leaf',
    'released',
    'in-progress',
    'mispackaged',
    'idle',
    'blocked',
)

def order_key(status_ident):
    try:
        return STATUS_ORDER.index(status_ident)
    except ValueError:
        return 4

def history_graph(query, status_query=(), title='History',
                  expand=False, show_percent=True):

    # Historical data can have statuses that aren't in the current DB,
    # so name/color/order may not always be available. Be forgiving.
    status_idents = []
    status_names = {s.ident: s.name for s in status_query}
    status_colors = {s.ident: s.color for s in status_query}
    status_colors.update({s.name: s.color for s in status_query})

    data = defaultdict(dict)
    for entry in query:
        date = entry.date[:10]
        data.setdefault(date, {})[entry.status] = entry.num_packages
        if entry.status not in status_idents:
            status_idents.append(entry.status)

    status_idents.sort(key=order_key)

    traces = [
        Scatter(
            name=status_names.get(ident, ident),
            x=[],
            y=[],
            text=[],
            hoverinfo=[],
            mode='lines',
            line=dict(width=0.5,
                      color=status_colors.get(ident, 'F0AD4E')),
            fill='tonexty',
        )
        for ident in status_idents]

    for date, values in data.items():
        running_total = 0
        total = sum(values.values())
        for trace, status_ident in zip(traces, status_idents):
            value = values.get(status_ident, 0)
            running_total += value
            trace.x.append(date)
            if expand:
                trace.y.append(100 * running_total / total)
            else:
                trace.y.append(running_total)
            if show_percent:
                trace.text.append('{value} ({percent}%)'.format(
                    value=value, percent=round(100 * value / total, 1)))
            else:
                trace.text.append(str(value))
            trace.hoverinfo.append('text+name' if value else 'skip')

    dates = list(data.keys())

    # HACK: An extra trace to show date on hover
    traces.append(Scatter(
        x=dates,
        y=[0 for d in dates],
        text=dates,
        hoverinfo='x',
        line=dict(width=0.5, color='fff'),
        name='',
    ))

    layout = Layout(
        title=title,
        hoverdistance=50,
        hovermode='x',
    )

    fig = Figure(data=traces, layout=layout)
    graph = plot(fig, output_type='div')
    return graph
