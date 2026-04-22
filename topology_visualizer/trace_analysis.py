#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

PROTOCOLS = ["UDP", "TCP", "ICMP"]


def _source_ip(source: Any) -> Optional[str]:
    if isinstance(source, str):
        return source
    if isinstance(source, dict):
        return source.get("ip")
    return None


def _source_geo(source: Any) -> Dict[str, Any]:
    if isinstance(source, dict):
        return {
            "ip": source.get("ip"),
            "lat": source.get("lat"),
            "lng": source.get("lng"),
            "city": source.get("city"),
            "region": source.get("region"),
            "country": source.get("country"),
        }
    return {
        "ip": source,
        "lat": None,
        "lng": None,
        "city": None,
        "region": None,
        "country": None,
    }


def _probe_geo_fields(probe: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "lat": probe.get("lat"),
        "lng": probe.get("lng"),
        "city": probe.get("city"),
        "region": probe.get("region"),
        "country": probe.get("country"),
        "country_iso": probe.get("country_iso"),
        "continent": probe.get("continent"),
        "asn": probe.get("asn"),
        "asn_org": probe.get("asn_org"),
    }


def to_dataframe(data: Dict[str, Any]) -> pd.DataFrame:
    rows: List[Dict[str, Any]] = []
    source = data.get("source")
    source_ip = _source_ip(source)
    source_geo = _source_geo(source)

    for dest in data.get("destinations", []):
        target = dest.get("target")
        target_name = dest.get("target_name")
        input_network = dest.get("input_network")
        input_kind = dest.get("input_kind")

        for probe in dest.get("probes", []):
            row = {
                "source_ip": source_ip,
                "source_lat": source_geo.get("lat"),
                "source_lng": source_geo.get("lng"),
                "source_city": source_geo.get("city"),
                "source_region": source_geo.get("region"),
                "source_country": source_geo.get("country"),
                "target": target,
                "target_name": target_name,
                "input_network": input_network,
                "input_kind": input_kind,
                "ttl": probe.get("ttl"),
                "series": probe.get("series", 1),
                "protocol": probe.get("protocol"),
                "success": bool(probe.get("success", False)),
                "reply_ip": probe.get("reply_ip"),
                "rtt_ms": probe.get("rtt_ms"),
                "reached_destination": bool(probe.get("reached_destination", False)),
                "icmp_type": probe.get("icmp_type"),
                "icmp_code": probe.get("icmp_code"),
            }
            row.update(_probe_geo_fields(probe))
            rows.append(row)

    df = pd.DataFrame(rows)
    if df.empty:
        raise ValueError("No probe rows found in input data.")
    return df


def shannon_entropy(values: List[Any]) -> Optional[float]:
    values = [v for v in values if pd.notna(v)]
    if not values:
        return None
    counts = pd.Series(values).value_counts(normalize=True)
    return float(-(counts * counts.apply(lambda p: math.log2(p))).sum())


def normalized_entropy(values: List[Any]) -> Optional[float]:
    values = [v for v in values if pd.notna(v)]
    unique_n = len(set(values))
    if unique_n <= 1:
        return 0.0 if values else None
    ent = shannon_entropy(values)
    if ent is None:
        return None
    return float(ent / math.log2(unique_n))


def summary_by_protocol(df: pd.DataFrame) -> List[Dict[str, Any]]:
    succ = df[df["success"]].copy()

    summary = (
        df.groupby("protocol", as_index=False)
        .agg(
            probes=("success", "size"),
            success_rate=("success", "mean"),
            loss_rate=("success", lambda s: 1 - s.mean()),
            dest_reached_rate=("reached_destination", "mean"),
        )
    )

    if not succ.empty:
        rtt = (
            succ.groupby("protocol", as_index=False)
            .agg(
                avg_rtt_ms=("rtt_ms", "mean"),
                median_rtt_ms=("rtt_ms", "median"),
                p95_rtt_ms=("rtt_ms", lambda s: s.quantile(0.95)),
                unique_reply_ips=("reply_ip", "nunique"),
            )
        )
        summary = summary.merge(rtt, on="protocol", how="left")

    return summary.sort_values("protocol").to_dict(orient="records")


def build_success_wide(df: pd.DataFrame) -> pd.DataFrame:
    ok = (
        df[df["success"] & df["reply_ip"].notna()][["target", "ttl", "protocol", "reply_ip"]]
        .drop_duplicates()
    )
    return ok.pivot_table(
        index=["target", "ttl"],
        columns="protocol",
        values="reply_ip",
        aggfunc="first",
    )


def pairwise_protocol_difference(wide: pd.DataFrame) -> List[Dict[str, Any]]:
    rows = []
    for a, b in [("UDP", "TCP"), ("UDP", "ICMP"), ("TCP", "ICMP")]:
        if a not in wide.columns or b not in wide.columns:
            continue
        both = wide[[a, b]].dropna()
        compared = len(both)
        if compared == 0:
            continue
        different = int((both[a] != both[b]).sum())
        rows.append({
            "pair": f"{a} vs {b}",
            "compared_target_ttls": int(compared),
            "different_reply_ip_count": different,
            "difference_ratio": float(different / compared),
        })
    return rows


def ttl_divergence(wide: pd.DataFrame) -> List[Dict[str, Any]]:
    rows = []
    reset = wide.reset_index()

    for ttl, g in reset.groupby("ttl"):
        compared = 0
        different = 0

        for _, row in g.iterrows():
            ips = [row.get(p) for p in PROTOCOLS if p in g.columns and pd.notna(row.get(p))]
            if len(ips) >= 2:
                compared += 1
                if len(set(ips)) > 1:
                    different += 1

        rows.append({
            "ttl": int(ttl),
            "compared_destinations": int(compared),
            "different_destinations": int(different),
            "difference_ratio": float(different / compared) if compared else None,
        })

    return rows


def destination_divergence(wide: pd.DataFrame) -> List[Dict[str, Any]]:
    rows = []
    reset = wide.reset_index()

    for target, g in reset.groupby("target"):
        compared = 0
        different = 0
        max_distinct = 0

        for _, row in g.iterrows():
            ips = [row.get(p) for p in PROTOCOLS if p in g.columns and pd.notna(row.get(p))]
            if len(ips) >= 2:
                compared += 1
                distinct = len(set(ips))
                max_distinct = max(max_distinct, distinct)
                if distinct > 1:
                    different += 1

        rows.append({
            "target": target,
            "compared_ttls": int(compared),
            "different_ttls": int(different),
            "difference_ratio": float(different / compared) if compared else None,
            "max_distinct_reply_ips_at_single_ttl": int(max_distinct),
        })

    rows.sort(key=lambda x: ((x["difference_ratio"] or -1), x["different_ttls"]), reverse=True)
    return rows


def path_stability_analysis(df: pd.DataFrame) -> Dict[str, Any]:
    """
    Within the same protocol:
    repeated probes at the same (target, ttl, protocol) may reveal
    instability / entropy, which is consistent with load balancing.
    """
    if "series" not in df.columns or int(df["series"].fillna(1).max()) <= 1:
        return {
            "has_multiple_series": False,
            "unstable_hops": [],
            "ttl_stability": [],
            "protocol_stability_summary": [],
        }

    ok = df[df["success"]].copy()
    if ok.empty:
        return {
            "has_multiple_series": True,
            "unstable_hops": [],
            "ttl_stability": [],
            "protocol_stability_summary": [],
        }

    rows = []
    for (target, ttl, protocol), g in ok.groupby(["target", "ttl", "protocol"]):
        ips = [x for x in g["reply_ip"].tolist() if pd.notna(x)]
        rows.append({
            "target": target,
            "ttl": int(ttl),
            "protocol": protocol,
            "probe_count": int(len(g)),
            "distinct_reply_ips": int(pd.Series(ips).nunique()) if ips else 0,
            "ip_entropy_norm": normalized_entropy(ips),
            "rtt_std_ms": float(g["rtt_ms"].std()) if len(g) > 1 and g["rtt_ms"].notna().any() else 0.0,
            "rtt_mean_ms": float(g["rtt_ms"].mean()) if g["rtt_ms"].notna().any() else None,
            "ip_stable": (len(set(ips)) <= 1) if ips else True,
        })

    grouped = pd.DataFrame(rows)

    ttl_stability = (
        grouped.groupby(["ttl", "protocol"], as_index=False)
        .agg(
            groups=("target", "size"),
            ip_stable_ratio=("ip_stable", "mean"),
            avg_rtt_std_ms=("rtt_std_ms", "mean"),
            avg_ip_entropy_norm=("ip_entropy_norm", "mean"),
        )
        .sort_values(["ttl", "protocol"])
    )

    protocol_summary = (
        grouped.groupby("protocol", as_index=False)
        .agg(
            groups=("target", "size"),
            ip_stable_ratio=("ip_stable", "mean"),
            avg_rtt_std_ms=("rtt_std_ms", "mean"),
            avg_ip_entropy_norm=("ip_entropy_norm", "mean"),
        )
        .sort_values("protocol")
    )

    unstable = grouped.sort_values(
        ["ip_entropy_norm", "distinct_reply_ips", "rtt_std_ms"],
        ascending=[False, False, False]
    )

    return {
        "has_multiple_series": True,
        "unstable_hops": unstable.head(100).to_dict(orient="records"),
        "ttl_stability": ttl_stability.to_dict(orient="records"),
        "protocol_stability_summary": protocol_summary.to_dict(orient="records"),
    }


def protocol_reachability(df: pd.DataFrame) -> Dict[str, Any]:
    reach = (
        df.groupby(["target", "protocol"], as_index=False)
        .agg(reached=("reached_destination", "max"))
    )

    pivot = reach.pivot(index="target", columns="protocol", values="reached").fillna(False)
    pivot = pivot.astype(bool)

    rows = []
    for target, row in pivot.iterrows():
        reached = [p for p in pivot.columns if bool(row[p])]
        rows.append({
            "target": target,
            "reached_protocols": reached,
            "reachability_pattern": "+".join(sorted(reached)) if reached else "none",
            "reached_protocol_count": len(reached),
            "reachability_asymmetry_score": 1 - (len(reached) / len(PROTOCOLS)),
        })

    dest_rows = pd.DataFrame(rows)

    pattern_summary = (
        dest_rows.groupby("reachability_pattern", as_index=False)
        .agg(destinations=("target", "size"))
        .sort_values("destinations", ascending=False)
    )

    return {
        "by_destination": dest_rows.sort_values(
            ["reachability_asymmetry_score", "target"], ascending=[False, True]
        ).to_dict(orient="records"),
        "pattern_summary": pattern_summary.to_dict(orient="records"),
    }


def rtt_increment_analysis(df: pd.DataFrame) -> Dict[str, Any]:
    ok = df[df["success"] & df["rtt_ms"].notna()].copy()
    if ok.empty:
        return {"ttl_average": [], "top_bottlenecks": []}

    ok = ok.sort_values(["target", "protocol", "series", "ttl"])
    ok["rtt_increment_ms"] = ok.groupby(["target", "protocol", "series"])["rtt_ms"].diff()

    increments = ok[ok["rtt_increment_ms"].notna()].copy()

    ttl_avg = (
        increments.groupby(["ttl", "protocol"], as_index=False)
        .agg(
            avg_rtt_increment_ms=("rtt_increment_ms", "mean"),
            median_rtt_increment_ms=("rtt_increment_ms", "median"),
            p95_rtt_increment_ms=("rtt_increment_ms", lambda s: s.quantile(0.95)),
            samples=("rtt_increment_ms", "size"),
        )
        .sort_values(["ttl", "protocol"])
    )

    bottlenecks = (
        increments.groupby(["target", "ttl", "protocol"], as_index=False)
        .agg(
            avg_rtt_increment_ms=("rtt_increment_ms", "mean"),
            max_rtt_increment_ms=("rtt_increment_ms", "max"),
            avg_rtt_ms=("rtt_ms", "mean"),
            hop_reply_ip=("reply_ip", lambda s: s.dropna().iloc[0] if len(s.dropna()) else None),
        )
        .sort_values(["max_rtt_increment_ms", "avg_rtt_increment_ms"], ascending=[False, False])
    )

    return {
        "ttl_average": ttl_avg.to_dict(orient="records"),
        "top_bottlenecks": bottlenecks.head(100).to_dict(orient="records"),
    }


def branching_analysis(df: pd.DataFrame) -> Dict[str, Any]:
    ok = df[df["success"] & df["reply_ip"].notna()].copy()
    if ok.empty:
        return {"ttl_level": [], "hotspots": []}

    per_dest_ttl = (
        ok.groupby(["target", "ttl"], as_index=False)
        .agg(
            distinct_reply_ips=("reply_ip", "nunique"),
            reply_ip_list=("reply_ip", lambda s: sorted(set(map(str, s)))[:10]),
        )
    )
    per_dest_ttl["is_branching"] = per_dest_ttl["distinct_reply_ips"] > 1

    ttl_branch = (
        per_dest_ttl.groupby("ttl", as_index=False)
        .agg(
            destinations_seen=("target", "nunique"),
            branching_destinations=("is_branching", "sum"),
            avg_distinct_reply_ips=("distinct_reply_ips", "mean"),
            max_distinct_reply_ips=("distinct_reply_ips", "max"),
        )
    )
    ttl_branch["branching_ratio"] = ttl_branch["branching_destinations"] / ttl_branch["destinations_seen"]

    hotspots = per_dest_ttl[per_dest_ttl["is_branching"]].sort_values(
        ["distinct_reply_ips", "target", "ttl"], ascending=[False, True, True]
    )

    return {
        "ttl_level": ttl_branch.to_dict(orient="records"),
        "hotspots": hotspots.head(100).to_dict(orient="records"),
    }


def common_hops(df: pd.DataFrame) -> List[Dict[str, Any]]:
    ok = df[df["success"] & df["reply_ip"].notna()].copy()
    if ok.empty:
        return []

    out = (
        ok.groupby("reply_ip", as_index=False)
        .agg(
            destinations=("target", "nunique"),
            appearances=("reply_ip", "size"),
            min_ttl=("ttl", "min"),
            max_ttl=("ttl", "max"),
            avg_rtt_ms=("rtt_ms", "mean"),
            countries=("country", lambda s: sorted({x for x in s.dropna() if x})[:10]),
            asns=("asn_org", lambda s: sorted({x for x in s.dropna() if x})[:5]),
        )
        .sort_values(["destinations", "appearances"], ascending=[False, False])
    )
    return out.head(100).to_dict(orient="records")


def abnormal_destinations(df: pd.DataFrame) -> List[Dict[str, Any]]:
    succ = df[df["success"]].copy()
    if succ.empty:
        return []

    by_dest_proto = (
        succ.groupby(["target", "protocol"], as_index=False)
        .agg(
            avg_rtt_ms=("rtt_ms", "mean"),
            median_rtt_ms=("rtt_ms", "median"),
            success_count=("success", "size"),
        )
    )

    pivot = by_dest_proto.pivot(index="target", columns="protocol", values="avg_rtt_ms")
    pivot.columns = [f"avg_rtt_{c.lower()}_ms" for c in pivot.columns]
    pivot = pivot.reset_index()

    dest_summary = (
        df.groupby("target", as_index=False)
        .agg(
            probes=("success", "size"),
            success_rate=("success", "mean"),
            loss_rate=("success", lambda s: 1 - s.mean()),
            reached_destination_rate=("reached_destination", "mean"),
        )
    )

    out = dest_summary.merge(pivot, on="target", how="left")

    def max_minus_min(row: pd.Series) -> Optional[float]:
        vals = [row.get(c) for c in row.index if str(c).startswith("avg_rtt_")]
        vals = [v for v in vals if pd.notna(v)]
        if len(vals) < 2:
            return None
        return float(max(vals) - min(vals))

    out["protocol_avg_rtt_gap_ms"] = out.apply(max_minus_min, axis=1)
    out = out.sort_values(["protocol_avg_rtt_gap_ms", "loss_rate"], ascending=[False, False])
    return out.head(100).to_dict(orient="records")


def icmp_behavior(df: pd.DataFrame) -> Dict[str, Any]:
    icmp_df = df[df["icmp_type"].notna()].copy()
    if icmp_df.empty:
        return {"icmp_type_counts": [], "by_protocol": []}

    type_counts = (
        icmp_df.groupby(["icmp_type", "icmp_code"], as_index=False)
        .agg(count=("icmp_type", "size"))
        .sort_values("count", ascending=False)
    )

    by_protocol = (
        icmp_df.groupby(["protocol", "icmp_type", "icmp_code"], as_index=False)
        .agg(count=("icmp_type", "size"))
        .sort_values(["protocol", "count"], ascending=[True, False])
    )

    return {
        "icmp_type_counts": type_counts.to_dict(orient="records"),
        "by_protocol": by_protocol.to_dict(orient="records"),
    }


def haversine_km(lat1, lon1, lat2, lon2) -> Optional[float]:
    vals = [lat1, lon1, lat2, lon2]
    if any(v is None or (isinstance(v, float) and math.isnan(v)) for v in vals):
        return None

    r = 6371.0
    phi1 = math.radians(float(lat1))
    phi2 = math.radians(float(lat2))
    dphi = math.radians(float(lat2) - float(lat1))
    dlambda = math.radians(float(lon2) - float(lon1))
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * r * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def geographic_analysis(df: pd.DataFrame) -> Dict[str, Any]:
    ok = df[df["success"] & df["reply_ip"].notna()].copy()
    if ok.empty:
        return {"countries": [], "continents": [], "distance_summary": {}, "top_geo_jumps": []}

    countries = (
        ok[ok["country"].notna()]
        .groupby("country", as_index=False)
        .agg(
            appearances=("country", "size"),
            destinations=("target", "nunique"),
        )
        .sort_values(["destinations", "appearances"], ascending=[False, False])
    )

    continents = (
        ok[ok["continent"].notna()]
        .groupby("continent", as_index=False)
        .agg(
            appearances=("continent", "size"),
            destinations=("target", "nunique"),
        )
        .sort_values(["destinations", "appearances"], ascending=[False, False])
    )

    src_lat = ok["source_lat"].dropna().iloc[0] if ok["source_lat"].notna().any() else None
    src_lng = ok["source_lng"].dropna().iloc[0] if ok["source_lng"].notna().any() else None

    if src_lat is not None and src_lng is not None:
        ok["distance_from_source_km"] = ok.apply(
            lambda r: haversine_km(src_lat, src_lng, r["lat"], r["lng"]), axis=1
        )
    else:
        ok["distance_from_source_km"] = None

    dist_valid = ok[ok["distance_from_source_km"].notna() & ok["rtt_ms"].notna()].copy()

    if len(dist_valid):
        distance_summary = {
            "samples": int(len(dist_valid)),
            "avg_distance_from_source_km": float(dist_valid["distance_from_source_km"].mean()),
            "avg_rtt_ms": float(dist_valid["rtt_ms"].mean()),
            "corr_distance_vs_rtt": float(dist_valid["distance_from_source_km"].corr(dist_valid["rtt_ms"]))
            if len(dist_valid) > 1 else None,
        }
    else:
        distance_summary = {
            "samples": 0,
            "avg_distance_from_source_km": None,
            "avg_rtt_ms": None,
            "corr_distance_vs_rtt": None,
        }

    valid_geo = ok[ok["lat"].notna() & ok["lng"].notna()].copy()
    valid_geo = valid_geo.sort_values(["target", "protocol", "series", "ttl"])

    jumps: List[Dict[str, Any]] = []
    for (target, protocol, series), g in valid_geo.groupby(["target", "protocol", "series"]):
        prev = None
        for _, row in g.iterrows():
            if prev is not None:
                jump_km = haversine_km(prev["lat"], prev["lng"], row["lat"], row["lng"])
                if jump_km is not None:
                    jumps.append({
                        "target": target,
                        "protocol": protocol,
                        "series": int(series),
                        "from_ttl": int(prev["ttl"]),
                        "to_ttl": int(row["ttl"]),
                        "from_ip": prev["reply_ip"],
                        "to_ip": row["reply_ip"],
                        "from_country": prev.get("country"),
                        "to_country": row.get("country"),
                        "jump_km": float(jump_km),
                        "to_rtt_ms": float(row["rtt_ms"]) if pd.notna(row["rtt_ms"]) else None,
                    })
            prev = row

    jumps.sort(key=lambda x: x["jump_km"], reverse=True)

    return {
        "countries": countries.head(50).to_dict(orient="records"),
        "continents": continents.head(20).to_dict(orient="records"),
        "distance_summary": distance_summary,
        "top_geo_jumps": jumps[:100],
    }


def infer_mechanisms(df: pd.DataFrame, wide: pd.DataFrame) -> Dict[str, Any]:
    """
    Conservative mechanism-oriented inference.

    FILTERING_LIKE:
        strong protocol-dependent reachability asymmetry

    LOAD_BALANCING_LIKE:
        high entropy / instability within the same protocol

    POLICY_ROUTING_LIKE:
        strong inter-protocol divergence with comparatively stable
        intra-protocol behavior

    MIXED_COMPLEX:
        moderate or combined signatures

    NORMAL_STABLE:
        weak evidence of protocol dependence / instability
    """
    stability = path_stability_analysis(df)
    reachability = protocol_reachability(df)
    divergence = destination_divergence(wide)

    target_stats = {}
    if stability.get("has_multiple_series"):
        for row in stability.get("unstable_hops", []):
            t = row["target"]
            target_stats.setdefault(t, {"stable_vals": [], "entropy_vals": []})
            target_stats[t]["stable_vals"].append(1.0 if row.get("ip_stable") else 0.0)
            ent = row.get("ip_entropy_norm")
            if ent is not None:
                target_stats[t]["entropy_vals"].append(ent)

    div_map = {row["target"]: row for row in divergence}
    reach_map = {row["target"]: row for row in reachability.get("by_destination", [])}

    targets = sorted(set(df["target"].dropna().tolist()))
    rows = []

    for target in targets:
        stat = target_stats.get(target, {})
        stable_vals = stat.get("stable_vals", [])
        entropy_vals = stat.get("entropy_vals", [])

        avg_entropy = float(sum(entropy_vals) / len(entropy_vals)) if entropy_vals else None
        avg_stable_ratio = float(sum(stable_vals) / len(stable_vals)) if stable_vals else None

        divergence_ratio = div_map.get(target, {}).get("difference_ratio")
        reach_asym = reach_map.get(target, {}).get("reachability_asymmetry_score", 0.0)

        mechanism = "NORMAL_STABLE"
        rationale = []

        if reach_asym is not None and reach_asym >= 0.34:
            mechanism = "FILTERING_LIKE"
            rationale.append("large reachability asymmetry across protocols")

        if avg_entropy is not None and avg_entropy >= 0.45:
            if mechanism == "NORMAL_STABLE":
                mechanism = "LOAD_BALANCING_LIKE"
            else:
                mechanism = "MIXED_COMPLEX"
            rationale.append("high intra-protocol path entropy")

        if (
            divergence_ratio is not None
            and divergence_ratio >= 0.5
            and avg_stable_ratio is not None
            and avg_stable_ratio >= 0.8
        ):
            if mechanism == "NORMAL_STABLE":
                mechanism = "POLICY_ROUTING_LIKE"
            elif mechanism in {"LOAD_BALANCING_LIKE", "FILTERING_LIKE"}:
                mechanism = "MIXED_COMPLEX"
            rationale.append("high inter-protocol divergence with stable intra-protocol paths")

        if mechanism == "NORMAL_STABLE" and (
            (divergence_ratio is not None and divergence_ratio >= 0.25) or
            (avg_entropy is not None and avg_entropy >= 0.2)
        ):
            mechanism = "MIXED_COMPLEX"
            rationale.append("moderate divergence/instability without a single dominant signature")

        rows.append({
            "target": target,
            "mechanism_label": mechanism,
            "avg_intra_protocol_entropy": avg_entropy,
            "avg_intra_protocol_stable_ratio": avg_stable_ratio,
            "inter_protocol_divergence_ratio": divergence_ratio,
            "reachability_asymmetry_score": reach_asym,
            "rationale": rationale,
        })

    mech_df = pd.DataFrame(rows)

    summary = (
        mech_df.groupby("mechanism_label", as_index=False)
        .agg(destinations=("target", "size"))
        .sort_values("destinations", ascending=False)
        .to_dict(orient="records")
    )

    return {
        "by_destination": mech_df.sort_values(
            ["mechanism_label", "inter_protocol_divergence_ratio"],
            ascending=[True, False]
        ).to_dict(orient="records"),
        "summary": summary,
    }


def mechanism_scatter_points(df: pd.DataFrame, wide: pd.DataFrame) -> List[Dict[str, Any]]:
    """
    Scatter plot coordinates for research-style mechanism map:
    x = intra-protocol instability = 1 - stable ratio
    y = inter-protocol divergence
    """
    stability = path_stability_analysis(df)
    divergence = destination_divergence(wide)

    if not stability.get("has_multiple_series"):
        return []

    target_stats = {}
    for row in stability.get("unstable_hops", []):
        t = row["target"]
        target_stats.setdefault(t, {"stable_vals": [], "entropy_vals": []})
        target_stats[t]["stable_vals"].append(1.0 if row.get("ip_stable") else 0.0)
        ent = row.get("ip_entropy_norm")
        if ent is not None:
            target_stats[t]["entropy_vals"].append(ent)

    div_map = {row["target"]: row for row in divergence}

    points = []
    for target, stat in target_stats.items():
        stable_ratio = sum(stat["stable_vals"]) / len(stat["stable_vals"]) if stat["stable_vals"] else None
        entropy = sum(stat["entropy_vals"]) / len(stat["entropy_vals"]) if stat["entropy_vals"] else None
        div = div_map.get(target, {}).get("difference_ratio")

        points.append({
            "target": target,
            "x_intra_protocol_instability": (1 - stable_ratio) if stable_ratio is not None else None,
            "y_inter_protocol_divergence": div,
            "avg_entropy": entropy,
        })

    return points


def representative_cases(analysis: Dict[str, Any]) -> Dict[str, Any]:
    """
    Select a few interpretable example targets.
    These are intended for map filtering, not for large tables.
    """
    rows = analysis.get("mechanism_inference", {}).get("by_destination", [])
    abnormal = analysis.get("abnormal_destinations", [])

    cases = []

    policy = next(
        (
            r for r in rows
            if r.get("mechanism_label") == "POLICY_ROUTING_LIKE"
            and (r.get("inter_protocol_divergence_ratio") or 0) >= 0.4
        ),
        None,
    )
    if policy:
        cases.append({
            "case_id": "policy_like",
            "title": "Policy-like example",
            "target": policy["target"],
            "mechanism_label": policy["mechanism_label"],
            "signature": "stable within protocol, but divergent across protocols",
            "interpretation": "consistent with protocol-dependent routing decisions",
        })

    load_bal = next(
        (
            r for r in rows
            if r.get("mechanism_label") == "LOAD_BALANCING_LIKE"
            and (r.get("avg_intra_protocol_entropy") or 0) >= 0.3
        ),
        None,
    )
    if load_bal and all(c["target"] != load_bal["target"] for c in cases):
        cases.append({
            "case_id": "load_balancing_like",
            "title": "Load-balancing-like example",
            "target": load_bal["target"],
            "mechanism_label": load_bal["mechanism_label"],
            "signature": "multiple next-hop responses within the same protocol",
            "interpretation": "consistent with repeated exposure to parallel paths",
        })

    filtering = next(
        (
            r for r in rows
            if r.get("mechanism_label") == "FILTERING_LIKE"
            and (r.get("reachability_asymmetry_score") or 0) >= 0.34
        ),
        None,
    )
    if filtering and all(c["target"] != filtering["target"] for c in cases):
        cases.append({
            "case_id": "filtering_like",
            "title": "Filtering-like example",
            "target": filtering["target"],
            "mechanism_label": filtering["mechanism_label"],
            "signature": "strong protocol-dependent reachability asymmetry",
            "interpretation": "consistent with filtering or firewall behavior",
        })

    high_latency = next(
        (
            r for r in abnormal
            if r.get("protocol_avg_rtt_gap_ms") is not None
        ),
        None,
    )
    if high_latency and all(c["target"] != high_latency["target"] for c in cases):
        cases.append({
            "case_id": "high_latency_gap",
            "title": "High-latency anomaly",
            "target": high_latency["target"],
            "mechanism_label": "LATENCY_ANOMALY",
            "signature": "large protocol-dependent RTT gap",
            "interpretation": "useful for inspecting protocol-sensitive latency effects",
        })

    return {
        "cases": cases[:3]
    }


def build_analysis(data: Dict[str, Any]) -> Dict[str, Any]:
    df = to_dataframe(data)
    wide = build_success_wide(df)

    analysis = {
        "meta": {
            "source": _source_geo(data.get("source")),
            "destination_count": len(data.get("destinations", [])),
            "probe_count": int(len(df)),
            "has_geolocation": bool(df["lat"].notna().any()),
            "has_multiple_series": bool(int(df["series"].fillna(1).max()) > 1),
        },
        "protocol_summary": summary_by_protocol(df),
        "pairwise_protocol_difference": pairwise_protocol_difference(wide),
        "ttl_divergence": ttl_divergence(wide),
        "destination_divergence": destination_divergence(wide),
        "branching": branching_analysis(df),
        "common_hops": common_hops(df),
        "abnormal_destinations": abnormal_destinations(df),
        "rtt_increment": rtt_increment_analysis(df),
        "path_stability": path_stability_analysis(df),
        "protocol_reachability": protocol_reachability(df),
        "icmp_behavior": icmp_behavior(df),
        "geography": geographic_analysis(df),
        "mechanism_inference": infer_mechanisms(df, wide),
        "mechanism_scatter": mechanism_scatter_points(df, wide),
    }

    analysis["representative_cases"] = representative_cases(analysis)
    return analysis


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("input_json", help="Path to traceroute JSON")
    parser.add_argument("--out", default="analysis.json", help="Path to write analysis JSON")
    args = parser.parse_args()

    with open(args.input_json, "r", encoding="utf-8") as f:
        data = json.load(f)

    analysis = build_analysis(data)

    out = Path(args.out)
    out.write_text(json.dumps(analysis, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote analysis to {out}")


if __name__ == "__main__":
    main()