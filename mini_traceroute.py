#!/usr/bin/env python3
import argparse
import csv
import errno
import ipaddress
import json
import os
import socket
import struct
import sys
import time
from typing import Any, Dict, List, Optional, Tuple

ICMP_ECHO_REPLY = 0
ICMP_DEST_UNREACH = 3
ICMP_ECHO_REQUEST = 8
ICMP_TIME_EXCEEDED = 11
DEFAULT_BASE_PORT = 33434


# -----------------------------
# Helpers
# -----------------------------

def checksum(data: bytes) -> int:
    if len(data) % 2 == 1:
        data += b"\x00"
    total = 0
    for i in range(0, len(data), 2):
        word = (data[i] << 8) + data[i + 1]
        total += word
        total = (total & 0xFFFF) + (total >> 16)
    return (~total) & 0xFFFF


def resolve_destination(host: str) -> str:
    return socket.gethostbyname(host)


def reverse_dns(ip: str, do_resolve: bool) -> Optional[str]:
    if not do_resolve:
        return None
    try:
        name, _, _ = socket.gethostbyaddr(ip)
        return name.rstrip(".")
    except Exception:
        return None


def get_source_ip() -> str:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("8.8.8.8", 80))
        return sock.getsockname()[0]
    except Exception:
        return "0.0.0.0"
    finally:
        sock.close()


def normalize_target(value: str, prefix_host_index: int = 1) -> Tuple[str, Dict[str, Any]]:
    raw = value.strip()
    try:
        network = ipaddress.ip_network(raw, strict=False)
        if isinstance(network, ipaddress.IPv6Network):
            raise ValueError("IPv6 targets are not supported by this script.")

        if network.prefixlen == 32:
            return str(network.network_address), {
                "input": raw,
                "target": str(network.network_address),
                "input_kind": "ipv4",
                "network": None,
            }

        hosts = list(network.hosts())
        if not hosts:
            return str(network.network_address), {
                "input": raw,
                "target": str(network.network_address),
                "input_kind": "prefix",
                "network": str(network),
            }

        index = max(1, prefix_host_index) - 1
        if index >= len(hosts):
            index = len(hosts) - 1
        chosen = str(hosts[index])
        return chosen, {
            "input": raw,
            "target": chosen,
            "input_kind": "prefix",
            "network": str(network),
        }
    except ValueError:
        return raw, {
            "input": raw,
            "target": raw,
            "input_kind": "hostname_or_literal",
            "network": None,
        }


# -----------------------------
# Input loading
# -----------------------------

def read_targets(single_destination: Optional[str], input_file: Optional[str], prefix_host_index: int) -> List[Dict[str, Any]]:
    if single_destination is not None:
        target, meta = normalize_target(single_destination, prefix_host_index)
        return [{"ip": target, "name": None, **meta}]

    if input_file is None:
        raise ValueError("Either a destination or --input file must be provided.")

    ext = os.path.splitext(input_file)[1].lower()
    targets: List[Dict[str, Any]] = []

    if ext == ".txt":
        with open(input_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                target, meta = normalize_target(line, prefix_host_index)
                targets.append({"ip": target, "name": None, **meta})

    elif ext == ".csv":
        with open(input_file, "r", encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            if reader.fieldnames is None:
                raise ValueError("CSV file is missing a header row.")

            field_map = {name.strip().lower(): name for name in reader.fieldnames}
            if "ip" not in field_map:
                raise ValueError("CSV file must contain an 'ip' column.")

            ip_key = field_map["ip"]
            name_key = field_map.get("name")
            for row in reader:
                raw_ip = (row.get(ip_key) or "").strip()
                if not raw_ip:
                    continue
                target, meta = normalize_target(raw_ip, prefix_host_index)
                name_value = ((row.get(name_key) or "").strip() if name_key else "") or None
                targets.append({"ip": target, "name": name_value, **meta})
    else:
        raise ValueError("Unsupported input file type. Use .txt or .csv")

    return targets


# -----------------------------
# Packet parsing
# -----------------------------

def parse_ipv4_header(packet: bytes) -> Tuple[int, int, str, str]:
    if len(packet) < 20:
        raise ValueError("Packet too short for IPv4 header")
    ver_ihl = packet[0]
    ihl = (ver_ihl & 0x0F) * 4
    proto = packet[9]
    src_ip = socket.inet_ntoa(packet[12:16])
    dst_ip = socket.inet_ntoa(packet[16:20])
    return ihl, proto, src_ip, dst_ip


def parse_icmp_message(packet: bytes) -> Dict[str, Any]:
    outer_ihl, outer_proto, outer_src, outer_dst = parse_ipv4_header(packet)
    if outer_proto != socket.IPPROTO_ICMP or len(packet) < outer_ihl + 8:
        raise ValueError("Not an ICMP packet")

    icmp_type, icmp_code, icmp_checksum = struct.unpack("!BBH", packet[outer_ihl:outer_ihl + 4])
    result: Dict[str, Any] = {
        "reply_ip": outer_src,
        "outer_dst": outer_dst,
        "icmp_type": icmp_type,
        "icmp_code": icmp_code,
        "icmp_checksum": icmp_checksum,
        "inner_protocol": None,
        "inner_src_port": None,
        "inner_dst_port": None,
        "echo_identifier": None,
        "echo_sequence": None,
    }

    if icmp_type in (ICMP_TIME_EXCEEDED, ICMP_DEST_UNREACH):
        inner_offset = outer_ihl + 8
        if len(packet) >= inner_offset + 20:
            inner_ihl, inner_proto, _, _ = parse_ipv4_header(packet[inner_offset:])
            result["inner_protocol"] = inner_proto
            transport_offset = inner_offset + inner_ihl
            if inner_proto in (socket.IPPROTO_UDP, socket.IPPROTO_TCP) and len(packet) >= transport_offset + 4:
                src_port, dst_port = struct.unpack("!HH", packet[transport_offset:transport_offset + 4])
                result["inner_src_port"] = src_port
                result["inner_dst_port"] = dst_port
            elif inner_proto == socket.IPPROTO_ICMP and len(packet) >= transport_offset + 8:
                inner_type, inner_code, _, ident, seq = struct.unpack(
                    "!BBHHH", packet[transport_offset:transport_offset + 8]
                )
                result["inner_icmp_type"] = inner_type
                result["inner_icmp_code"] = inner_code
                result["echo_identifier"] = ident
                result["echo_sequence"] = seq

    elif icmp_type == ICMP_ECHO_REPLY and len(packet) >= outer_ihl + 8:
        _, _, _, ident, seq = struct.unpack("!BBHHH", packet[outer_ihl:outer_ihl + 8])
        result["echo_identifier"] = ident
        result["echo_sequence"] = seq

    return result


# -----------------------------
# Sockets and probes
# -----------------------------

def create_icmp_receive_socket(timeout: float) -> socket.socket:
    sock = socket.socket(socket.AF_INET, socket.SOCK_RAW, socket.IPPROTO_ICMP)
    sock.settimeout(timeout)
    return sock


def create_udp_send_socket(ttl: int) -> socket.socket:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.setsockopt(socket.SOL_IP, socket.IP_TTL, ttl)
    return sock


def create_icmp_send_socket(ttl: int) -> socket.socket:
    sock = socket.socket(socket.AF_INET, socket.SOCK_RAW, socket.IPPROTO_ICMP)
    sock.setsockopt(socket.SOL_IP, socket.IP_TTL, ttl)
    return sock


def create_tcp_send_socket(ttl: int, timeout: float) -> socket.socket:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP)
    sock.setsockopt(socket.SOL_IP, socket.IP_TTL, ttl)
    sock.settimeout(timeout)
    return sock


def build_icmp_echo(identifier: int, sequence: int, payload: bytes) -> bytes:
    header = struct.pack("!BBHHH", ICMP_ECHO_REQUEST, 0, 0, identifier, sequence)
    packet = header + payload
    packet = struct.pack("!BBHHH", ICMP_ECHO_REQUEST, 0, checksum(packet), identifier, sequence) + payload
    return packet


def wait_for_matching_icmp(
    recv_sock: socket.socket,
    timeout: float,
    matcher,
) -> Optional[Dict[str, Any]]:
    end_deadline = time.perf_counter() + timeout
    while True:
        remaining = end_deadline - time.perf_counter()
        if remaining <= 0:
            return None
        recv_sock.settimeout(remaining)
        try:
            packet, _ = recv_sock.recvfrom(65535)
            parsed = parse_icmp_message(packet)
            if matcher(parsed):
                return parsed
        except socket.timeout:
            return None
        except Exception:
            continue


def make_probe_result(
    protocol: str,
    ttl: int,
    series: int,
    reply_ip: Optional[str],
    reply_name: Optional[str],
    rtt_ms: Optional[float],
    success: bool,
    icmp_type: Optional[int] = None,
    icmp_code: Optional[int] = None,
    reached_destination: bool = False,
) -> Dict[str, Any]:
    result: Dict[str, Any] = {
        "ttl": ttl,
        "series": series,
        "protocol": protocol,
        "success": success,
    }
    if reply_ip is not None:
        result["reply_ip"] = reply_ip
    if reply_name is not None:
        result["reply_name"] = reply_name
    if rtt_ms is not None:
        result["rtt_ms"] = round(rtt_ms, 3)
    if icmp_type is not None:
        result["icmp_type"] = icmp_type
    if icmp_code is not None:
        result["icmp_code"] = icmp_code
    if reached_destination:
        result["reached_destination"] = True
    return result


def probe_once_udp(
    dest_ip: str,
    ttl: int,
    timeout: float,
    payload: bytes,
    dst_port: int,
    resolve_names: bool,
) -> Dict[str, Any]:
    recv_sock = create_icmp_receive_socket(timeout)
    send_sock = create_udp_send_socket(ttl)
    try:
        start = time.perf_counter()
        send_sock.sendto(payload, (dest_ip, dst_port))

        reply = wait_for_matching_icmp(
            recv_sock,
            timeout,
            matcher=lambda msg: msg.get("inner_protocol") == socket.IPPROTO_UDP and msg.get("inner_dst_port") == dst_port,
        )
        if reply is None:
            return make_probe_result("UDP", ttl, 1, None, None, None, False)

        rtt_ms = (time.perf_counter() - start) * 1000.0
        reply_ip = reply["reply_ip"]
        reply_name = reverse_dns(reply_ip, resolve_names)
        reached = reply["icmp_type"] == ICMP_DEST_UNREACH and reply_ip == dest_ip
        return make_probe_result(
            "UDP",
            ttl,
            1,
            reply_ip,
            reply_name,
            rtt_ms,
            True,
            icmp_type=reply.get("icmp_type"),
            icmp_code=reply.get("icmp_code"),
            reached_destination=reached,
        )
    finally:
        send_sock.close()
        recv_sock.close()


def probe_once_icmp(
    dest_ip: str,
    ttl: int,
    timeout: float,
    payload: bytes,
    identifier: int,
    sequence: int,
    resolve_names: bool,
) -> Dict[str, Any]:
    recv_sock = create_icmp_receive_socket(timeout)
    send_sock = create_icmp_send_socket(ttl)
    try:
        packet = build_icmp_echo(identifier, sequence, payload)
        start = time.perf_counter()
        send_sock.sendto(packet, (dest_ip, 0))

        reply = wait_for_matching_icmp(
            recv_sock,
            timeout,
            matcher=lambda msg: (
                (msg.get("icmp_type") == ICMP_ECHO_REPLY and msg.get("echo_identifier") == identifier and msg.get("echo_sequence") == sequence)
                or (
                    msg.get("inner_protocol") == socket.IPPROTO_ICMP
                    and msg.get("echo_identifier") == identifier
                    and msg.get("echo_sequence") == sequence
                )
            ),
        )
        if reply is None:
            return make_probe_result("ICMP", ttl, 1, None, None, None, False)

        rtt_ms = (time.perf_counter() - start) * 1000.0
        reply_ip = reply["reply_ip"]
        reply_name = reverse_dns(reply_ip, resolve_names)
        reached = reply.get("icmp_type") == ICMP_ECHO_REPLY and reply_ip == dest_ip
        return make_probe_result(
            "ICMP",
            ttl,
            1,
            reply_ip,
            reply_name,
            rtt_ms,
            True,
            icmp_type=reply.get("icmp_type"),
            icmp_code=reply.get("icmp_code"),
            reached_destination=reached,
        )
    finally:
        send_sock.close()
        recv_sock.close()


def probe_once_tcp(
    dest_ip: str,
    ttl: int,
    timeout: float,
    dst_port: int,
    resolve_names: bool,
) -> Dict[str, Any]:
    recv_sock = create_icmp_receive_socket(timeout)
    send_sock = create_tcp_send_socket(ttl, timeout)
    try:
        start = time.perf_counter()
        try:
            send_sock.connect((dest_ip, dst_port))
            rtt_ms = (time.perf_counter() - start) * 1000.0
            reply_name = reverse_dns(dest_ip, resolve_names)
            return make_probe_result("TCP", ttl, 1, dest_ip, reply_name, rtt_ms, True, reached_destination=True)
        except ConnectionRefusedError:
            rtt_ms = (time.perf_counter() - start) * 1000.0
            reply_name = reverse_dns(dest_ip, resolve_names)
            return make_probe_result("TCP", ttl, 1, dest_ip, reply_name, rtt_ms, True, reached_destination=True)
        except OSError as e:
            if getattr(e, "errno", None) in {errno.ECONNREFUSED}:
                rtt_ms = (time.perf_counter() - start) * 1000.0
                reply_name = reverse_dns(dest_ip, resolve_names)
                return make_probe_result("TCP", ttl, 1, dest_ip, reply_name, rtt_ms, True, reached_destination=True)

        local_port = send_sock.getsockname()[1]
        reply = wait_for_matching_icmp(
            recv_sock,
            timeout,
            matcher=lambda msg: msg.get("inner_protocol") == socket.IPPROTO_TCP and msg.get("inner_src_port") == local_port,
        )
        if reply is None:
            return make_probe_result("TCP", ttl, 1, None, None, None, False)

        rtt_ms = (time.perf_counter() - start) * 1000.0
        reply_ip = reply["reply_ip"]
        reply_name = reverse_dns(reply_ip, resolve_names)
        reached = reply_ip == dest_ip and reply.get("icmp_type") == ICMP_DEST_UNREACH
        return make_probe_result(
            "TCP",
            ttl,
            1,
            reply_ip,
            reply_name,
            rtt_ms,
            True,
            icmp_type=reply.get("icmp_type"),
            icmp_code=reply.get("icmp_code"),
            reached_destination=reached,
        )
    finally:
        send_sock.close()
        recv_sock.close()


# -----------------------------
# Output formatting
# -----------------------------

def hop_label(name: Optional[str], ip: Optional[str]) -> Optional[str]:
    if ip is None:
        return None
    return f"{name} ({ip})" if name else ip


def format_hop_tokens(probes: List[Dict[str, Any]]) -> List[str]:
    tokens: List[str] = []
    last_label: Optional[str] = None

    for probe in probes:
        if not probe.get("success") or probe.get("rtt_ms") is None:
            tokens.append("*")
            last_label = None
            continue

        label = hop_label(probe.get("reply_name"), probe.get("reply_ip"))
        rtt_token = f"{probe['rtt_ms']:.3f} ms"

        if label != last_label and label is not None:
            tokens.append(label)
        tokens.append(rtt_token)
        last_label = label

    return tokens


def print_hop_line(ttl: int, probes: List[Dict[str, Any]]) -> None:
    tokens = format_hop_tokens(probes)
    print(f"{ttl:2d}  {'  '.join(tokens)}")


# -----------------------------
# Main trace logic
# -----------------------------

def trace_one_destination(
    destination_ip: str,
    destination_name: Optional[str],
    first_ttl: int,
    max_ttl: int,
    timeout: float,
    base_port: int,
    payload_size: int,
    num_series: int,
    delay: float,
    resolve_names_flag: bool,
) -> Dict[str, Any]:
    resolved_ip = resolve_destination(destination_ip)
    payload = b"A" * payload_size
    target_name = destination_name or reverse_dns(resolved_ip, resolve_names_flag)
    identifier = os.getpid() & 0xFFFF

    print(f"traceroute to {target_name or destination_ip} ({resolved_ip}), {max_ttl} hops max, {payload_size} byte packets")

    destination_result: Dict[str, Any] = {
        "target": resolved_ip,
        "target_name": target_name,
        "probes": [],
    }

    reached_destination = False

    for ttl in range(first_ttl, max_ttl + 1):
        line_probes: List[Dict[str, Any]] = []
        for series in range(1, num_series + 1):
            unique_port = base_port + ((ttl - 1) * num_series + (series - 1)) * 3
            udp_result = probe_once_udp(resolved_ip, ttl, timeout, payload, unique_port, resolve_names_flag)
            udp_result["series"] = series
            line_probes.append(udp_result)
            destination_result["probes"].append(udp_result)
            if delay > 0:
                time.sleep(delay)

            tcp_result = probe_once_tcp(resolved_ip, ttl, timeout, unique_port + 1, resolve_names_flag)
            tcp_result["series"] = series
            line_probes.append(tcp_result)
            destination_result["probes"].append(tcp_result)
            if delay > 0:
                time.sleep(delay)

            icmp_result = probe_once_icmp(resolved_ip, ttl, timeout, payload, identifier, (ttl - 1) * num_series + series, resolve_names_flag)
            icmp_result["series"] = series
            line_probes.append(icmp_result)
            destination_result["probes"].append(icmp_result)
            if delay > 0:
                time.sleep(delay)

            if any(p.get("reached_destination") for p in (udp_result, tcp_result, icmp_result)):
                reached_destination = True

        print_hop_line(ttl, line_probes)
        if reached_destination:
            break

    print()
    return destination_result


# -----------------------------
# JSON output
# -----------------------------

def write_results_json(results: Dict[str, Any], output_file: str) -> None:
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)


# -----------------------------
# CLI
# -----------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Traceroute-like tool that reads destinations from a TXT/CSV file and saves JSON for visualization."
    )
    parser.add_argument("destination", nargs="?", help="Single destination hostname, IPv4 address, or IPv4 prefix")
    parser.add_argument("--input", dest="input_file", help="TXT/CSV file containing destinations")
    parser.add_argument("--first-ttl", type=int, default=1, help="Initial TTL")
    parser.add_argument("--max-ttl", type=int, default=64, help="Maximum TTL")
    parser.add_argument("--timeout", type=float, default=2.0, help="Probe timeout in seconds")
    parser.add_argument("--port", type=int, default=DEFAULT_BASE_PORT, help="Base destination port used for UDP/TCP probes")
    parser.add_argument("--payload-size", type=int, default=52, help="Probe payload size in bytes")
    parser.add_argument("--delay", type=float, default=0.0, help="Delay between consecutive probes in seconds")
    parser.add_argument("--num-series", type=int, default=1, help="Number of UDP/TCP/ICMP series per TTL")
    parser.add_argument("--output", default="sample_data.json", help="Output JSON file")
    parser.add_argument("--prefix-host-index", type=int, default=1, help="When an input line is a prefix like x.x.x.x/24, probe the Nth usable host (default: 1)")
    parser.add_argument("-n", "--no-dns", action="store_true", help="Do not resolve hostnames")

    args = parser.parse_args()
    if args.destination is None and args.input_file is None:
        parser.error("Provide either a destination or --input <file>.")
    if args.destination is not None and args.input_file is not None:
        parser.error("Use either a single destination or --input <file>, not both.")
    return args


def main() -> None:
    args = parse_args()

    if os.geteuid() != 0:
        print("Error: raw sockets require root privileges. Please run with sudo.", file=sys.stderr)
        sys.exit(1)

    targets = read_targets(args.destination, args.input_file, args.prefix_host_index)
    all_results: Dict[str, Any] = {
        "source": get_source_ip(),
        "destinations": [],
    }

    for target in targets:
        try:
            destination_result = trace_one_destination(
                destination_ip=target["ip"],
                destination_name=target.get("name"),
                first_ttl=args.first_ttl,
                max_ttl=args.max_ttl,
                timeout=args.timeout,
                base_port=args.port,
                payload_size=args.payload_size,
                num_series=args.num_series,
                delay=args.delay,
                resolve_names_flag=not args.no_dns,
            )
            destination_result["input"] = target.get("input")
            destination_result["input_kind"] = target.get("input_kind")
            if target.get("network"):
                destination_result["input_network"] = target.get("network")
            all_results["destinations"].append(destination_result)
        except socket.gaierror as e:
            print(f"Skipping target '{target['ip']}': name resolution failed ({e})", file=sys.stderr)
        except KeyboardInterrupt:
            raise
        except Exception as e:
            print(f"Skipping target '{target['ip']}': unexpected error ({e})", file=sys.stderr)

    write_results_json(all_results, args.output)
    print(f"Saved raw results to: {args.output}")


if __name__ == "__main__":
    main()
