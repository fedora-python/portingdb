from collections import OrderedDict, Counter, defaultdict
import random
import math
import uuid
import datetime

from flask import Flask, render_template, current_app, Markup, abort, url_for
from flask import make_response, request
from flask.json import jsonify
from jinja2 import StrictUndefined
import markdown
import networkx

from .history_graph import history_graph
from .load_data import get_data, DONE_STATUSES, PY2_STATUSES

PAGE_NAME = 'Python 2 Dropping Database'
tau = 2 * math.pi


def group_by_status(package_list):
    by_status = defaultdict(list)
    for package in package_list:
        by_status[package['status']].append(package)
    return by_status


def summarize_statuses(statuses, package_list):
    by_status = group_by_status(package_list)
    return [
        (status, len(by_status[name]))
        for name, status in statuses.items()
        if len(by_status[name])
    ]


def summarize_2_dual_3(package_list):
    """
    Given list of packages, return counts of (py3-only, dual-support, py2-only)
    """
    py3 = 0
    dual = 0
    py2 = 0
    for pkg in package_list:
        if pkg['status'] == 'py3-only':
            py3 += 1
        elif pkg['status'] in PY2_STATUSES:
            dual += 1
        else:
            py2 += 1
    return py3, dual, py2


def last_link_update_sort_key(package):
    return (
        -bool(package['last_link_update']),
        package['last_link_update'],
    )


def hello():
    data = current_app.config['data']

    statuses = data['statuses']
    packages = data['packages']

    by_status = group_by_status(packages.values())

    the_score = len(by_status['py3-only']) / len(packages)
    py2_score = sum(len(by_status[s]) for s in PY2_STATUSES) / len(packages)

    status_summary = summarize_statuses(statuses, packages.values())

    def sort_key(item):
        return item[0]['hidden'], item[0]['name']
    groups = sorted((
        (grp, summarize_2_dual_3(grp['packages'].values()))
        for grp in data['groups'].values()
    ), key=sort_key)

    naming_progress = get_naming_policy_info(data)

    return render_template(
        'index.html',
        breadcrumbs=(
            (url_for('hello'), PAGE_NAME),
        ),
        statuses=statuses,
        total_pkg_count=len(packages),
        status_summary=status_summary,
        ready_packages=by_status.get('idle', ()),
        blocked_packages=by_status.get('blocked', ()),
        py3_only_packages=by_status.get('py3-only', ()),
        legacy_leaf_packages=by_status.get('legacy-leaf', ()),
        released_packages=by_status.get('released', ()),
        dropped_packages=by_status.get('dropped', ()),
        mispackaged_packages=sorted(
            by_status.get('mispackaged', ()),
            key=last_link_update_sort_key),
        groups=groups,
        the_score=the_score,
        py2_score=py2_score,
        naming_progress=naming_progress,
    )


def jsonstats():
    data = current_app.config['data']

    packages = data['packages']
    grouped = group_by_status(packages.values())

    stats = {
        status: len(packages)
        for status, packages in grouped.items()
    }

    return jsonify(stats)


def get_status_counts(pkgs):
    counted = Counter(p.status_obj for p in pkgs)
    ordered = OrderedDict(sorted(counted.items(),
                                 key=lambda s_n: s_n[0].order))
    return ordered


def generate_deptree(
    package, *, keys=('deps', 'build_deps'),
    skip_statuses=frozenset({'idle'} | DONE_STATUSES),
    max_depth=3,
    seen=None,
):
    if seen is None:
        seen = set()
    def generate_subtree(pkg, depth):
        run_names = set(pkg[keys[0]])
        build_names = set(pkg[keys[1]])
        packages = {
            pkg['name']: pkg
            for pkg in list(pkg[keys[0]].values()) + list(pkg[keys[1]].values())
        }
        children = sorted(
            packages.values(),
            key=status_sort_key,
        )
        for child in children:
            kinds = set()
            if child['name'] in run_names:
                kinds.add('run')
            if child['name'] in build_names:
                kinds.add('build')

            was_seen = (child['name'] in seen)
            seen.add(child['name'])

            if child['status'] in skip_statuses:
                tree = ()
            elif was_seen or depth >= max_depth:
                tree = ()
                kinds.add('elided')
            else:
                tree = list(generate_subtree(child, depth+1))

            yield child, kinds, tree

    return list(generate_subtree(package, 0))

def generate_deptrees(
    packages, skip_statuses=frozenset({'idle'} | DONE_STATUSES), **kwargs
):
    packages = sorted(packages, key=status_sort_key)
    seen = set()
    for pkg in packages:
        if pkg['status'] in skip_statuses:
            tree = ()
        else:
            tree = generate_deptree(
                pkg, skip_statuses=skip_statuses, seen=seen, **kwargs
            )
        yield pkg, set(), tree


def package(pkg):
    data = current_app.config['data']
    statuses = data['statuses']

    try:
        package = data['packages'][pkg]
    except KeyError:
        abort(404)

    return render_template(
        'package.html',
        breadcrumbs=(
            (url_for('hello'), PAGE_NAME),
            (url_for('package', pkg=pkg), pkg),
        ),
        pkg=package,
        deptree=generate_deptree(package),
        dependencies_status_counts=summarize_statuses(statuses, package['deps'].values()),
        build_dependencies_status_counts=summarize_statuses(
            statuses, package['build_deps'].values()),
        dependent_tree=generate_deptree(
            package,
            keys=('dependents', 'build_dependents'),
            skip_statuses=frozenset(('py3-only', 'dropped')),
        ),
    )


def group(grp):
    data = current_app.config['data']

    try:
        group = data['groups'][grp]
    except KeyError:
        abort(404)

    statuses = data['statuses']
    status_summary = summarize_statuses(statuses, group['packages'].values())

    return render_template(
        'group.html',
        breadcrumbs=(
            (url_for('hello'), PAGE_NAME),
            (url_for('group', grp=grp), group['name']),
        ),
        grp=group,
        deptree=list(generate_deptrees(
            group['seed_packages'].values(),
            skip_statuses=set(),
            max_depth=7,
        )),
        status_summary=status_summary,
    )


def markdown_filter(text):
    return Markup(markdown.markdown(text))


def status_sort_key(package):
    return -package['status_obj']['weight'], package['name']

def sort_by_status(packages):
    return sorted(packages, key=status_sort_key)


def format_rpm_name(text):
    name, version, release = text.rsplit('-', 2)
    return Markup('<span class="rpm-name">{}</span>-{}-{}'.format(
        name, version, release))


def format_time_ago(date):
    """Displays roughly how long ago the date was in a human readable format"""
    now = datetime.datetime.utcnow()
    diff = now - date

    # Years
    if diff.days >= 365:
        if diff.days >= 2 * 365:
            return "{} years ago".format(math.floor(diff.days / 365))
        else:
            return "a year ago"
    # Months
    elif diff.days >= 31:
        if diff.days >= 2 * 30:
            return "{} months ago".format(math.floor(diff.days / 30))
        else:
            return "a month ago"
    # Weeks
    elif diff.days >= 7:
        if diff.days >= 2 * 7:
            return "{} weeks ago".format(math.floor(diff.days / 7))
        else:
            return "a week ago"
    # Days
    elif diff.days >= 2:
        return "{} days ago".format(diff.days)
    elif diff.days == 1:
        return "yesterday"
    else:
        return "today"


def graph(grp=None, pkg=None):
    # Parameters
    all_deps = request.args.get('all_deps', None)
    if all_deps not in ('1', None):
        abort(400)  # Bad request

    return render_template(
        'graph.html',
        breadcrumbs=(
            (url_for('hello'), PAGE_NAME),
            (url_for('graph'), 'Graph'),
        ),
        grp=grp,
        pkg=pkg,
        all_deps=all_deps,
    )


def graph_json(grp=None, pkg=None):
    data = current_app.config['data']
    packages = data['packages']

    # Get a list of all dependency relationships, as pairs of package names.
    # Package names will be later used as nodes of several graphs.
    link_pairs = [
        (dep_name, pkg_name)
        for pkg_name, pkg_dict in packages.items()
        for attr in ('dependents', 'build_dependents')
        for dep_name in pkg_dict[attr]
    ]

    # Find "tiers" of packages: if the Python 2 dropping was done by removing
    # all leaf packages at once, a package's "tier" is how many of those
    # removals would be needed to drop it.
    # "Clusters" of packages that form cycles are removed at once.

    # For each such cluster, find some "representative" (rep) that will
    # identify the cluster.
    # (The rep will be the first node in the cluster, in alphabetical order.)
    reps = {name: name for name in packages}

    # The cluster that has python2 is quite complex.
    # Networkx's `simple_cycles` will have a **lot** less work if we
    # strategically remove these packages from the graph, and put them in
    # python2's cluster manually.
    usual_suspects = [
        'python-sphinx',
        'pytest',
        'python-setuptools',
        'python-mock',
    ]
    for name in usual_suspects:
        reps[name] = 'python2'

    # Build a graph where all nodes are replaced by their representative
    # (ignoring duplicate edges and self-loops)
    graph = networkx.DiGraph()
    graph.add_edges_from(set(
        (reps[a], reps[b]) for a, b in link_pairs if reps[a] != reps[b]
    ))

    # For all cycles in this graph, replace all nodes in the cycle by a
    # representative.
    for cycle in networkx.cycles.simple_cycles(graph):
        rep = min(reps[name] for name in cycle)
        for name in cycle:
            reps[name] = rep

    # Since python2's rep probably changed, adjust the nodes previously removed
    for name in usual_suspects:
        reps[name] = reps['python2']

    # Build a graph whose nodes are the representatives (standing for clusters)
    # (ignoring duplicate edges and self-loops)
    cluster_graph = networkx.DiGraph()
    cluster_graph.add_edges_from(set(
        (reps[a], reps[b]) for a, b in link_pairs if reps[a] != reps[b]
    ))

    # Iteratively remove the cluster graph's leaves, keeping track of which
    # iteration each node was removed in.
    tiers = {}
    tier = 0
    while cluster_graph.nodes:
        tier += 1
        leaves = [
            n for n, d in cluster_graph.in_degree(cluster_graph.nodes)
            if d == 0
        ]
        print(len(leaves), 'clusters in tier', tier)
        if not leaves:
            # Theoretically, we should have an acyclic graph here, so it
            # should be possible to remove leaves until the graph is empty.
            # However, because of the "usual_suspects" stuff above, something
            # can be left over. Just put it in the last tier.
            print(len(cluster_graph.nodes), 'clusters remaining')
            for rep in cluster_graph.nodes:
                tiers[rep] = tier
            break
        for rep in leaves:
            if tier == 1 and packages[rep]['status'] in DONE_STATUSES:
                # The first tier is separated into py3-only/legacy-leaf
                # (tier=0) idle/blocked (tier=1) to make the outside of the
                # graph look less crowded
                tiers[rep] = 0
            else:
                tiers[rep] = tier
        cluster_graph.remove_nodes_from(leaves)

    # Having found the tier numbers, abandon cluster_graph.
    # Convert a new, full graph (from link_pairs) for use in JS.
    nodes = []
    node_indices = {}
    for i, node in enumerate(set(node for pair in link_pairs for node in pair)):
        node_indices[node] = i
        tier = tiers.get(reps[node])
        pkg = packages[node]
        color = pkg['status_obj']['color']
        nodes.append({
            'name': node,
            'color': graph_color(pkg['status_obj']['color'], tier),
            'status_color': '#' + pkg['status_obj']['color'],
            'tier': tier,
        })
    links = [
        {
            "source": node_indices[src],
            "target": node_indices[target],
        }
        for src, target in link_pairs
    ]

    return jsonify(nodes=nodes, links=links)


def graph_color(color, depth):
    def component_color(c):
        c /= 255
        c = c ** (1.1 ** depth)
        c *= 255
        return '{0:02x}'.format(int(c))

    sc = color
    return '#' + ''.join(component_color(int(sc[x:x+2], 16))
                         for x in (0, 2, 4))


def _piechart(status_summary, bg=None):
    total_pkg_count = sum(c for s, c in status_summary)
    resp = make_response(render_template(
        'piechart.svg',
        status_summary=status_summary,
        total_pkg_count=total_pkg_count or 1,
        sin=math.sin, cos=math.cos, tau=tau,
        bg=bg,
    ))
    resp.headers['Content-type'] = 'image/svg+xml'
    return resp


def status_svg(status):
    data = current_app.config['data']
    try:
        status = data['statuses'][status]
    except KeyError:
        abort(404)

    return _piechart([], status)


def piechart_svg():
    data = current_app.config['data']
    statuses = data['statuses']
    packages = data['packages']

    status_summary = summarize_statuses(statuses, packages.values())

    return _piechart(status_summary)


def piechart_grp(grp):
    data = current_app.config['data']
    statuses = data['statuses']

    try:
        group = data['groups'][grp]
    except KeyError:
        abort(404)

    status_summary = summarize_statuses(statuses, group['packages'].values())

    return _piechart(status_summary)


def howto():
    data = current_app.config['data']
    statuses = data['statuses']
    packages = data['packages']

    by_status = group_by_status(packages.values())

    return render_template(
        'howto.html',
        breadcrumbs=(
            (url_for('hello'), PAGE_NAME),
            (url_for('howto'), 'So you want to contribute?'),
        ),
        idle_len=len(by_status['idle']),
        blocked_len=len(by_status['blocked']),
        mispackaged=len(by_status['mispackaged']),
        by_status=by_status,
        statuses=statuses,
        random_mispackaged=random.choice(by_status['mispackaged']),
        random_idle=random.choice(by_status['idle']),
    )


def history(expand=False):
    data = current_app.config['data']

    graph = history_graph(
        entries=data['history'],
        statuses=data['statuses'],
        title='portingdb history',
        expand=bool(expand),
    )

    return render_template(
        'history.html',
        graph=graph,
        expand=bool(expand),
        breadcrumbs=(
            (url_for('hello'), PAGE_NAME),
            (url_for('history'), 'History'),
        ),
    )


def mispackaged():
    # List of mispackaged packages. This page is not very useful any more,
    # but left for URL stability.
    data = current_app.config['data']

    mispackaged = [p for p in data['packages'].values()
                   if p['status'] == 'mispackaged']
    mispackaged.sort(key=last_link_update_sort_key)

    return render_template(
        'mispackaged.html',
        breadcrumbs=(
            (url_for('hello'), PAGE_NAME),
            (url_for('mispackaged', requested=1), 'Mispackaged'),
        ),
        mispackaged=mispackaged,
    )


def namingpolicy():
    """Naming policy tracking.
    """
    data = current_app.config['data']

    misnamed = {
        name: pkg for (name, pkg) in data['packages'].items()
        if pkg['is_misnamed']
    }
    requires_unblocked = {
        name: pkg for (name, pkg) in data['packages'].items()
        if pkg['unversioned_requires'] and not pkg['blocked_requires']
    }
    requires_blocked = {
        name: pkg for (name, pkg) in data['packages'].items()
        if pkg['blocked_requires']
    }
    nonpython = {
        name: any(p['is_misnamed'] for p in pkgs.values())
        for name, pkgs in data['non_python_unversioned_requires'].items()
    }

    return render_template(
        'namingpolicy.html',
        breadcrumbs=(
            (url_for('hello'), PAGE_NAME),
            (url_for('namingpolicy'), 'Naming Policy'),
        ),
        misnamed=misnamed,
        requires_unblocked=requires_unblocked,
        requires_blocked=requires_blocked,
        nonpython=nonpython,
    )


def get_naming_policy_info(data):
    naming_statuses = data['naming_statuses']
    progress = {
        'name-misnamed': 0,
        'require-misnamed': 0,
        'require-blocked': 0,
    }
    for name, package in data['packages'].items():
        if package['is_misnamed']:
            progress['name-misnamed'] += 1

        if package['blocked_requires']:
            progress['require-blocked'] += 1
        elif package['unversioned_requires']:
            progress['require-misnamed'] += 1

    return tuple(
        (naming_statuses[name], count)
        for name, count in progress.items()
    )


def piechart_namingpolicy():
    data = current_app.config['data']
    summary = get_naming_policy_info(data)
    return _piechart(summary)


def history_naming():
    data = current_app.config['data']

    graph = history_graph(
        entries=data['history-naming'],
        statuses=data['naming'],
        title='portingdb naming history',
        show_percent=False,
    )

    return render_template(
        'history-naming.html',
        graph=graph,
        breadcrumbs=(
            (url_for('hello'), PAGE_NAME),
            (url_for('namingpolicy'), 'Naming Policy'),
            (url_for('history'), 'History'),
        )
    )


def maintainer(name):
    data = current_app.config['data']
    try:
        maintainer = data['maintainers'][name]
    except KeyError:
        abort(404)

    # dict of {comaintainer name: (comaintainer, dict of {pkg name: package})}
    comaintainers = {}
    for pkg in maintainer['packages'].values():
        for comaintainer in pkg['maintainers'].values():
            if comaintainer['name'] != name:
                _c, pkgs = comaintainers.setdefault(
                    comaintainer['name'], (comaintainer, {}))
                pkgs[pkg['name']] = pkg
    return render_template(
        'maintainer.html',
        maintainer=maintainer,
        comaintainers=comaintainers,
        breadcrumbs=(
            (url_for('hello'), PAGE_NAME),
            (url_for('maintainer', name=name), name),
        )
    )


def format_quantity(num):
    for prefix in ' KMGT':
        if num > 1000:
            num /= 1000
        else:
            break
    if num > 100:
        num = round(num)
    elif num > 10:
        num = round(num, 1)
    else:
        num = round(num, 2)
    if abs(num - int(num)) < 0.01:
        num = int(num)
    return str(num) + prefix


def format_percent(num):
    num *= 100
    if num > 10:
        num = round(num)
    if num > 1:
        num = round(num, 1)
        if abs(num - int(num)) < 0.01:
            num = int(num)
    else:
        for digits in range(1, 3):
            rounded = round(num, digits)
            if rounded != 0:
                break
        num = rounded
    return str(num) + '%'


def split_digits(number, digits_after_dp=1):
    """Return digits before and after a decimal point"""
    number = round(float(number), digits_after_dp)
    return str(number).split('.')

assert split_digits(1) == ['1', '0']
assert split_digits(1.234) == ['1', '2']
assert split_digits(1234.56) == ['1234', '6']
assert split_digits(1234.56, 2) == ['1234', '56']
assert split_digits(73.98) == ['74', '0']
assert split_digits(-8.5) == ['-8', '5']


def create_app(directories, cache_config=None):
    app = Flask(__name__)
    app.config['data'] = data = get_data('data/')
    app.config['CONFIG'] = data['config']
    app.jinja_env.undefined = StrictUndefined
    app.jinja_env.filters['md'] = markdown_filter
    app.jinja_env.filters['format_rpm_name'] = format_rpm_name
    app.jinja_env.filters['format_quantity'] = format_quantity
    app.jinja_env.filters['format_percent'] = format_percent
    app.jinja_env.filters['format_time_ago'] = format_time_ago
    app.jinja_env.filters['sort_by_status'] = sort_by_status
    app.jinja_env.filters['split_digits'] = split_digits
    app.jinja_env.filters['summarize_statuses'] = (
        lambda p: summarize_statuses(data['statuses'], p))

    @app.context_processor
    def add_template_globals():
        return {
            'cache_tag': uuid.uuid4(),
            'len': len,
            'log': math.log,
            'config': app.config['CONFIG'],
        }

    def _add_route(url, func, **kwargs):
        app.route(url, **kwargs)(func)

    _add_route("/", hello)
    _add_route("/stats.json", jsonstats)
    _add_route("/pkg/<pkg>/", package)
    _add_route("/grp/<grp>/", group)
    _add_route("/graph/", graph)
    _add_route("/graph/portingdb.json", graph_json)
    _add_route("/piechart.svg", piechart_svg)
    _add_route("/status/<status>.svg", status_svg)
    _add_route("/grp/<grp>/piechart.svg", piechart_grp)
    _add_route("/mispackaged/", mispackaged)
    _add_route("/namingpolicy/", namingpolicy)
    _add_route("/namingpolicy/piechart.svg", piechart_namingpolicy)
    _add_route("/namingpolicy/history/", history_naming)
    _add_route("/history/", history, defaults={'expand': False})
    _add_route("/history/expanded/", history, defaults={'expand': True})
    _add_route("/howto/", howto)
    _add_route("/maintainer/<name>/", maintainer)

    return app


def main(directories, cache_config=None, debug=False, port=5000):
    app = create_app(directories)
    app.run(debug=debug, port=port)
