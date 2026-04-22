#!/usr/bin/env python3
import argparse
import ipaddress
import json
import sys
from pathlib import Path
from typing import Any, Dict, Optional


import geoip2.database
import geoip2.errors


def is_public_ip(ip: str) -> bool:
    """
    Return True only for globally routable public IPs.
    """
    try:
        addr = ipaddress.ip_address(ip)
        return addr.is_global
    except ValueError:
        return False


def lookup_ip(reader: geoip2.database.Reader, ip: str) -> Optional[Dict[str, Any]]:
    """
    Look up one IP in a MaxMind City database and return a compact dict.

    Returned fields are approximate geolocation metadata suitable for plotting.
    """
    try:
        response = reader.city(ip)
    except (
        geoip2.errors.AddressNotFoundError,
        ValueError,
    ):
        return None

    loc = response.location
    city = response.city
    country = response.country
    subdivision = response.subdivisions.most_specific
    continent = response.continent
    traits = response.traits

    if loc.latitude is None or loc.longitude is None:
        return None

    return {
        "ip": ip,
        "lat": loc.latitude,
        "lng": loc.longitude,
        "city": city.name,
        "country": country.name,
        "country_iso": country.iso_code,
        "region": subdivision.name,
        "region_iso": subdivision.iso_code,
        "continent": continent.name,
        "timezone": loc.time_zone,
        "accuracy_radius_km": loc.accuracy_radius,
        "network": str(response.traits.network) if response.traits.network else None,
        "asn": getattr(traits, "autonomous_system_number", None),
        "asn_org": getattr(traits, "autonomous_system_organization", None),
    }

def lookup_asn(reader: geoip2.database.Reader, ip: str) -> Optional[Dict[str, Any]]:
    try:
        response = reader.asn(ip)
    except (
        geoip2.errors.AddressNotFoundError,
        ValueError,
    ):
        return None

    return {
        "asn": response.autonomous_system_number,
        "asn_org": response.autonomous_system_organization,
    }

def enrich_ip_value(
    value: Any,
    city_reader: geoip2.database.Reader,
    asn_reader: geoip2.database.Reader,
    cache: Dict[str, Optional[Dict[str, Any]]],
) -> Any:
    """
    Enrich a single IP value.

    If `value` is a string public IP and lookup succeeds, return a dict with geo fields.
    Otherwise return the original value.
    """
    if not isinstance(value, str):
        return value

    ip = value.strip()
    if not ip or not is_public_ip(ip):
        return value

    if ip not in cache:
        geo = lookup_ip(city_reader, ip)
        asn = lookup_asn(asn_reader, ip)

        merged = {}
        if geo:
            merged.update(geo)
        if asn:
            merged.update(asn)

        cache[ip] = merged if merged else None

    geo = cache[ip]
    if not geo:
        return value

    return geo


def enrich_trace_data(data: Dict[str, Any], city_reader: geoip2.database.Reader, asn_reader: geoip2.database.Reader) -> Dict[str, Any]:
    """
    Enrich source and all eligible probe entries in-place and return the updated structure.
    """
    cache: Dict[str, Optional[Dict[str, Any]]] = {}

    # Enrich source
    if "source" in data:
        data["source"] = enrich_ip_value(
            data["source"], city_reader, asn_reader, cache
        )

    # Enrich probe reply IPs
    destinations = data.get("destinations", [])
    for dst in destinations:
        for probe in dst.get("probes", []):
            reply_ip = probe.get("reply_ip")
            success = bool(probe.get("success"))

            if not success or not reply_ip:
                continue

            if not is_public_ip(reply_ip):
                # Skip private, loopback, link-local, multicast, reserved, etc.
                continue

            if reply_ip not in cache:
                geo = lookup_ip(city_reader, reply_ip)
                asn = lookup_asn(asn_reader, reply_ip)

                merged = {}
                if geo:
                    merged.update(geo)
                if asn:
                    merged.update(asn)

                cache[reply_ip] = merged if merged else None

            geo = cache[reply_ip]
            if not geo:
                continue

            for key, value in geo.items():
                if key == "ip":
                    continue
                if key not in probe or probe[key] in (None, "", []):
                    probe[key] = value

    return data


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Enrich traceroute JSON with offline MaxMind GeoLite2 City geolocation."
    )
    parser.add_argument(
        "input_json",
        type=Path,
        help="Path to raw traceroute JSON file",
    )
    parser.add_argument(
        "output_json",
        type=Path,
        help="Path to write enriched JSON file",
    )
    parser.add_argument(
        "--mmdb",
        type=Path,
        required=True,
        help="Path to MaxMind GeoLite2-City.mmdb (or GeoIP2 City) database file",
    )
    parser.add_argument(
        "--asn-mmdb",
        type=Path,
        required=True,
        help="Path to GeoLite2-ASN.mmdb",
    )
    parser.add_argument(
        "--indent",
        type=int,
        default=2,
        help="JSON indentation level for output",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    if not args.input_json.exists():
        print(f"Input JSON not found: {args.input_json}", file=sys.stderr)
        return 1

    if not args.mmdb.exists():
        print(f"MaxMind database not found: {args.mmdb}", file=sys.stderr)
        return 1

    with args.input_json.open("r", encoding="utf-8") as f:
        data = json.load(f)

    with geoip2.database.Reader(str(args.mmdb)) as city_reader, \
        geoip2.database.Reader(str(args.asn_mmdb)) as asn_reader:

        enriched = enrich_trace_data(data, city_reader, asn_reader)

    with args.output_json.open("w", encoding="utf-8") as f:
        json.dump(enriched, f, ensure_ascii=False, indent=args.indent)

    print(f"Enriched JSON written to: {args.output_json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())