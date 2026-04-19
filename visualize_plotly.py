#!/usr/bin/env python3
import csv
import argparse
import json
from collections import defaultdict, Counter
from typing import Any, Dict, List, Optional, Tuple

import networkx as nx
import plotly.graph_objects as go


SOURCE_NODE = "SOURCE"
PROTOCOLS = ["udp", "tcp", "icmp"]


def load_results(path: str) -> List[Dict[str, Any]]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)
    
def load_hostmap(path: Optional[str]) -> Dict[str, str]:
    """
    Read a CSV file with columns:
      ip,name

    Returns:
      { "10.0.1.1": "r1", ... }
    """
    if path is None:
        return {}

    hostmap: Dict[str, str] = {}

    with open(path, "r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)

        if reader.fieldnames is None:
            raise ValueError("Hostmap CSV is missing a header row.")

        fieldnames = [x.strip().lower() for x in reader.fieldnames]
        if "ip" not in fieldnames or "name" not in fieldnames:
            raise ValueError("Hostmap CSV must contain 'ip' and 'name' columns.")

        field_map = {x.strip().lower(): x for x in reader.fieldnames}
        ip_key = field_map["ip"]
        name_key = field_map["name"]

        for row in reader:
            ip = row.get(ip_key, "")
            name = row.get(name_key, "")

            ip = ip.strip() if ip else ""
            name = name.strip() if name else ""

            if ip and name:
                hostmap[ip] = name

    return hostmap
    

def build_destination_name_map(results: List[Dict[str, Any]]) -> Dict[str, str]:
    """
    Build a mapping:
      destination_ip -> destination_name

    Keeps only non-empty names.
    """
    name_map: Dict[str, str] = {}

    for rec in results:
        ip = rec.get("destination_ip")
        name = rec.get("destination_name")

        if ip and name:
            name_map[ip] = name

    return name_map


def group_by_destination_and_ttl(
    results: List[Dict[str, Any]],
) -> Dict[str, Dict[int, List[Dict[str, Any]]]]:
    grouped: Dict[str, Dict[int, List[Dict[str, Any]]]] = defaultdict(lambda: defaultdict(list))
    for rec in results:
        grouped[rec["destination"]][rec["ttl"]].append(rec)
    return grouped


def choose_representative_hop(records_for_ttl: List[Dict[str, Any]]) -> Optional[str]:
    """
    For one destination and TTL, choose the most frequent non-null responder IP
    across all protocols and all series.
    """
    ips: List[str] = []

    for rec in records_for_ttl:
        for proto in PROTOCOLS:
            ip = rec.get(proto, {}).get("responder_ip")
            if ip is not None:
                ips.append(ip)

    if not ips:
        return None

    return Counter(ips).most_common(1)[0][0]


def summarize_records(records_for_ttl: List[Dict[str, Any]]) -> Dict[str, Dict[str, Optional[float]]]:
    """
    Compute per-protocol average RTT and success rate for one destination+TTL.
    """
    summary: Dict[str, Dict[str, Optional[float]]] = {}

    for proto in PROTOCOLS:
        total = 0
        successes = 0
        rtts: List[float] = []

        for rec in records_for_ttl:
            total += 1
            pdata = rec.get(proto, {})
            if not pdata.get("timeout", True):
                successes += 1
                rtt = pdata.get("rtt_ms")
                if rtt is not None:
                    rtts.append(float(rtt))

        avg_rtt = sum(rtts) / len(rtts) if rtts else None
        success_rate = successes / total if total > 0 else None

        summary[proto] = {
            "avg_rtt_ms": avg_rtt,
            "success_rate": success_rate,
        }

    return summary


def classify_edge_behavior(metrics: Dict[str, Dict[str, Optional[float]]]) -> Tuple[str, str]:
    """
    Return:
      behavior_label, edge_color
    """
    good = []

    for proto in PROTOCOLS:
        sr = metrics[proto]["success_rate"]
        if sr is not None and sr >= 0.75:
            good.append(proto)

    good_set = set(good)

    if good_set == {"udp", "tcp", "icmp"}:
        return "all protocols responsive", "#2ca02c"
    if good_set == {"udp", "tcp"}:
        return "UDP+TCP responsive, ICMP weak", "#f0a202"
    if good_set == {"udp", "icmp"}:
        return "UDP+ICMP responsive, TCP weak", "#1f77b4"
    if good_set == {"tcp", "icmp"}:
        return "TCP+ICMP responsive, UDP weak", "#9467bd"
    if len(good_set) == 1:
        only_proto = next(iter(good_set))
        return f"mostly {only_proto.upper()} responsive", "#d62728"

    return "mixed / unclear", "#7f7f7f"


def build_clean_topology(
    results: List[Dict[str, Any]],
    hostmap: Dict[str, str],
) -> Tuple[nx.DiGraph, Dict[Tuple[str, str], Dict[str, Any]], Dict[str, str]]:
    grouped = group_by_destination_and_ttl(results)
    destination_name_map = build_destination_name_map(results)

    G = nx.DiGraph()
    G.add_node(SOURCE_NODE, label="SOURCE", kind="source")

    node_targets: Dict[str, set] = defaultdict(set)
    node_target_names: Dict[str, Dict[str, str]] = defaultdict(dict)
    edge_observations: Dict[Tuple[str, str], List[Dict[str, Any]]] = defaultdict(list)

    for destination, ttl_map in grouped.items():
        sorted_ttls = sorted(ttl_map.keys())
        previous_node = SOURCE_NODE

        for ttl in sorted_ttls:
            records_for_ttl = ttl_map[ttl]
            rep_hop = choose_representative_hop(records_for_ttl)

            if rep_hop is None:
                continue

            node_targets[rep_hop].add(destination)

            # If this destination has a friendly name, store it
            dest_name = None
            for rec in records_for_ttl:
                maybe_name = rec.get("destination_name")
                if maybe_name:
                    dest_name = maybe_name
                    break
            if dest_name:
                node_target_names[rep_hop][destination] = dest_name

            metrics = summarize_records(records_for_ttl)

            if previous_node != rep_hop:
                edge_observations[(previous_node, rep_hop)].append({
                    "destination": destination,
                    "destination_name": dest_name,
                    "ttl": ttl,
                    "metrics": metrics,
                })

            previous_node = rep_hop

    for ip, dests in node_targets.items():
        target_name_map = node_target_names.get(ip, {})
        friendly_name = hostmap.get(ip) or destination_name_map.get(ip)

        if friendly_name:
            label = f"{friendly_name}\n{ip}"
        else:
            label = ip

        G.add_node(
            ip,
            label=label,
            kind="hop",
            target_count=len(dests),
            targets=sorted(dests),
            target_name_map=target_name_map,
            friendly_name=friendly_name,
        )

    edge_details: Dict[Tuple[str, str], Dict[str, Any]] = {}

    for (u, v), observations in edge_observations.items():
        proto_success_values: Dict[str, List[float]] = defaultdict(list)
        proto_rtt_values: Dict[str, List[float]] = defaultdict(list)
        dests = set()
        dest_name_map: Dict[str, str] = {}
        ttls = []

        for obs in observations:
            dests.add(obs["destination"])
            if obs.get("destination_name"):
                dest_name_map[obs["destination"]] = obs["destination_name"]
            ttls.append(obs["ttl"])
            metrics = obs["metrics"]

            for proto in PROTOCOLS:
                sr = metrics[proto]["success_rate"]
                avg_rtt = metrics[proto]["avg_rtt_ms"]

                if sr is not None:
                    proto_success_values[proto].append(float(sr))
                if avg_rtt is not None:
                    proto_rtt_values[proto].append(float(avg_rtt))

        merged_metrics: Dict[str, Dict[str, Optional[float]]] = {}
        for proto in PROTOCOLS:
            sr_vals = proto_success_values.get(proto, [])
            rtt_vals = proto_rtt_values.get(proto, [])

            merged_metrics[proto] = {
                "success_rate": sum(sr_vals) / len(sr_vals) if sr_vals else None,
                "avg_rtt_ms": sum(rtt_vals) / len(rtt_vals) if rtt_vals else None,
            }

        behavior_label, color = classify_edge_behavior(merged_metrics)

        all_rtts = [
            merged_metrics[p]["avg_rtt_ms"]
            for p in PROTOCOLS
            if merged_metrics[p]["avg_rtt_ms"] is not None
        ]
        mean_rtt = sum(all_rtts) / len(all_rtts) if all_rtts else None

        if mean_rtt is None:
            width = 2.0
        else:
            width = 8.0 / (1.0 + 0.15 * mean_rtt)
            width = max(1.5, min(6.0, width))

        G.add_edge(
            u,
            v,
            color=color,
            width=width,
            behavior=behavior_label,
            mean_rtt=mean_rtt,
        )

        edge_details[(u, v)] = {
            "behavior": behavior_label,
            "color": color,
            "destinations": sorted(dests),
            "destination_name_map": dest_name_map,
            "ttl_range": (min(ttls), max(ttls)) if ttls else None,
            "metrics": merged_metrics,
            "mean_rtt": mean_rtt,
        }

    return G, edge_details, destination_name_map


def classify_endpoints(G: nx.DiGraph) -> set:
    """
    Detect endpoint IPs: nodes that are themselves listed in their target set.
    """
    endpoint_ips = set()

    for node, attrs in G.nodes(data=True):
        if node == SOURCE_NODE:
            continue
        targets = attrs.get("targets", [])
        if node in targets:
            endpoint_ips.add(node)

    return endpoint_ips


def graphviz_positions_or_fallback(G: nx.DiGraph) -> Dict[str, Tuple[float, float]]:
    """
    Prefer Graphviz 'dot' for tree-like layout. Fall back to spring_layout if unavailable.
    """
    try:
        pos = nx.nx_agraph.graphviz_layout(G, prog="dot")
        return {node: (float(x), float(y)) for node, (x, y) in pos.items()}
    except Exception:
        pos = nx.spring_layout(G, seed=42, k=2.0)
        return {node: (float(x), float(y)) for node, (x, y) in pos.items()}


def make_node_hover_text(
    node: str,
    attrs: Dict[str, Any],
    endpoint_ips: set,
) -> str:
    if node == SOURCE_NODE:
        return "<b>SOURCE</b><br>Probing machine"

    node_type = "Endpoint" if node in endpoint_ips else "Router / intermediate hop"
    friendly_name = attrs.get("friendly_name")

    title = f"<b>{friendly_name} ({node})</b>" if friendly_name else f"<b>{node}</b>"

    targets = attrs.get("targets", [])
    target_name_map = attrs.get("target_name_map", {})

    if targets:
        target_lines = []
        for t in targets:
            if t in target_name_map:
                target_lines.append(f"{target_name_map[t]} ({t})")
            else:
                target_lines.append(t)
        target_text = "<br>".join(target_lines)
    else:
        target_text = "None"

    return (
        f"{title}<br>"
        f"Type: {node_type}<br>"
        f"Appears on paths to {len(targets)} target(s)<br>"
        f"Targets:<br>{target_text}"
    )


def make_edge_hover_text(u: str, v: str, info: Dict[str, Any]) -> str:
    dest_name_map = info.get("destination_name_map", {})
    destinations = info.get("destinations", [])

    if destinations:
        dest_items = []
        for d in destinations:
            if d in dest_name_map:
                dest_items.append(f"{dest_name_map[d]} ({d})")
            else:
                dest_items.append(d)
        dest_text = ", ".join(dest_items)
    else:
        dest_text = "None"

    lines = [
        f"<b>{u} → {v}</b>",
        f"Behavior: {info['behavior']}",
        f"Destinations: {dest_text}",
        f"TTL range: {info['ttl_range']}",
    ]

    mean_rtt = info["mean_rtt"]
    lines.append(
        f"Mean RTT across responsive protocols: "
        f"{'None' if mean_rtt is None else f'{mean_rtt:.2f} ms'}"
    )

    for proto in PROTOCOLS:
        sr = info["metrics"][proto]["success_rate"]
        rtt = info["metrics"][proto]["avg_rtt_ms"]
        sr_text = "None" if sr is None else f"{sr:.2f}"
        rtt_text = "None" if rtt is None else f"{rtt:.2f} ms"
        lines.append(f"{proto.upper()}: success={sr_text}, avg_rtt={rtt_text}")

    return "<br>".join(lines)


def build_plotly_figure(
    G: nx.DiGraph,
    edge_details: Dict[Tuple[str, str], Dict[str, Any]],
    destination_name_map: Dict[str, str],
    title: str,
) -> go.Figure:
    pos = graphviz_positions_or_fallback(G)
    endpoint_ips = classify_endpoints(G)

    fig = go.Figure()

    # Draw edges one by one so each can have its own hover text, color, width
    for u, v, attrs in G.edges(data=True):
        x0, y0 = pos[u]
        x1, y1 = pos[v]

        info = edge_details[(u, v)]
        hover_text = make_edge_hover_text(u, v, info)

        fig.add_trace(
            go.Scatter(
                x=[x0, x1],
                y=[y0, y1],
                mode="lines",
                line=dict(
                    color=attrs.get("color", "#7f7f7f"),
                    width=attrs.get("width", 2.0) * 2.0,  # a bit stronger in Plotly
                ),
                hoverinfo="text",
                text=[hover_text, hover_text],
                name=attrs.get("behavior", ""),
                showlegend=False,
            )
        )

    # Draw nodes by category to allow different styles
    categories = {
        "source": {
            "nodes": [],
            "color": "#ffcc80",
            "size": 76,
            "symbol": "circle",
            "name": "SOURCE",
        },
        "endpoint": {
            "nodes": [],
            "color": "#8ddf8d",
            "size": 76,
            "symbol": "circle",
            "name": "Endpoints",
        },
        "router": {
            "nodes": [],
            "color": "#9ecae1",
            "size": 76,
            "symbol": "circle",
            "name": "Routers / hops",
        },
    }

    for node, attrs in G.nodes(data=True):
        if node == SOURCE_NODE:
            categories["source"]["nodes"].append((node, attrs))
        elif node in endpoint_ips:
            categories["endpoint"]["nodes"].append((node, attrs))
        else:
            categories["router"]["nodes"].append((node, attrs))

    for cat in categories.values():
        if not cat["nodes"]:
            continue

        xs = []
        ys = []
        texts = []
        hover_texts = []

        for node, attrs in cat["nodes"]:
            x, y = pos[node]
            xs.append(x)
            ys.append(y)
            texts.append(attrs.get("label", node))
            hover_texts.append(make_node_hover_text(node, attrs, endpoint_ips))

        fig.add_trace(
            go.Scatter(
                x=xs,
                y=ys,
                mode="markers+text",
                text=texts,
                textposition="middle center",
                hoverinfo="text",
                hovertext=hover_texts,
                marker=dict(
                    size=cat["size"],
                    color=cat["color"],
                    line=dict(color="white", width=2),
                    symbol=cat["symbol"],
                ),
                name=cat["name"],
            )
        )

    # Manual legend for edge behavior
    legend_behaviors = [
        ("all protocols responsive", "#2ca02c"),
        ("UDP+TCP responsive, ICMP weak", "#f0a202"),
        ("UDP+ICMP responsive, TCP weak", "#1f77b4"),
        ("TCP+ICMP responsive, UDP weak", "#9467bd"),
        ("mostly one protocol responsive", "#d62728"),
        ("mixed / unclear", "#7f7f7f"),
    ]

    for name, color in legend_behaviors:
        fig.add_trace(
            go.Scatter(
                x=[None],
                y=[None],
                mode="lines",
                line=dict(color=color, width=4),
                name=name,
                showlegend=True,
                hoverinfo="skip",
            )
        )

    fig.update_layout(
        title=dict(
            text=(
                f"{title}<br>"
                f"<sup>Nodes are merged by IP. Hover over nodes and edges for details. "
                f"Edge color summarizes protocol behavior and edge thickness reflects RTT.</sup>"
            ),
            x=0.5,
        ),
        template="plotly_white",
        showlegend=True,
        hovermode="closest",
        margin=dict(l=30, r=30, t=90, b=30),
        xaxis=dict(showgrid=False, zeroline=False, visible=False),
        yaxis=dict(showgrid=False, zeroline=False, visible=False),
    )

    return fig


def main() -> None:
    parser = argparse.ArgumentParser(description="Interactive Plotly topology visualizer")
    parser.add_argument("--input", default="results.json", help="Input JSON file")
    parser.add_argument("--output", default="topology_interactive.html", help="Output HTML file")
    parser.add_argument("--title", default="Interactive Merged Topology Map", help="Figure title")
    parser.add_argument("--hostmap", default="hostmap.csv", help="Optional CSV file with columns ip,name for labeling routers/hosts")
    args = parser.parse_args()

    hostmap = load_hostmap(args.hostmap)
    results = load_results(args.input)
    G, edge_details, destination_name_map = build_clean_topology(results, hostmap)
    fig = build_plotly_figure(G, edge_details, destination_name_map, args.title)
    fig.write_html(args.output, include_plotlyjs=True)
    print(f"Saved interactive HTML graph to: {args.output}")


if __name__ == "__main__":
    main()