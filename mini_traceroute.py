#!/usr/bin/env python3
import argparse
import errno
import json
import os
import socket
import struct
import time
import csv
from typing import Optional, Tuple, List, Dict, Any


def resolve_destination(host: str) -> str:
    """Resolve hostname/IP string to IPv4 address."""
    return socket.gethostbyname(host)


def checksum(data: bytes) -> int:
    """Compute ICMP checksum."""
    if len(data) % 2 == 1:
        data += b"\x00"

    s = 0
    for i in range(0, len(data), 2):
        word = (data[i] << 8) + data[i + 1]
        s += word
        s = (s & 0xFFFF) + (s >> 16)

    return ~s & 0xFFFF


def create_icmp_receive_socket(timeout: float) -> socket.socket:
    """Create raw ICMP receive socket."""
    recv_sock = socket.socket(socket.AF_INET, socket.SOCK_RAW, socket.IPPROTO_ICMP)
    recv_sock.settimeout(timeout)
    return recv_sock


def create_udp_send_socket(ttl: int) -> socket.socket:
    """Create UDP send socket with given TTL."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.setsockopt(socket.SOL_IP, socket.IP_TTL, ttl)
    return sock


def create_icmp_send_socket(ttl: int) -> socket.socket:
    """Create raw ICMP send socket with given TTL."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_RAW, socket.IPPROTO_ICMP)
    sock.setsockopt(socket.SOL_IP, socket.IP_TTL, ttl)
    return sock


def build_icmp_echo(identifier: int, sequence: int, payload: bytes) -> bytes:
    """Build ICMP Echo Request packet."""
    icmp_type = 8
    icmp_code = 0
    chksum = 0

    header = struct.pack("!BBHHH", icmp_type, icmp_code, chksum, identifier, sequence)
    packet = header + payload
    chksum = checksum(packet)

    header = struct.pack("!BBHHH", icmp_type, icmp_code, chksum, identifier, sequence)
    return header + payload


def probe_once_udp(
    dest_ip: str,
    ttl: int,
    port: int,
    timeout: float,
    payload: bytes,
) -> Tuple[Optional[str], Optional[float], bool]:
    """
    Send one UDP probe with a controlled TTL.
    Returns:
        responder_ip, rtt_ms, reached_destination
    """
    recv_sock = create_icmp_receive_socket(timeout)
    send_sock = create_udp_send_socket(ttl)

    try:
        recv_sock.bind(("", 0))

        start = time.perf_counter()
        send_sock.sendto(payload, (dest_ip, port))
        _, addr = recv_sock.recvfrom(4096)
        end = time.perf_counter()

        responder_ip = addr[0]
        rtt_ms = (end - start) * 1000.0
        reached = responder_ip == dest_ip

        return responder_ip, rtt_ms, reached

    except socket.timeout:
        return None, None, False

    finally:
        send_sock.close()
        recv_sock.close()


def probe_once_icmp(
    dest_ip: str,
    ttl: int,
    timeout: float,
    payload: bytes,
    sequence: int,
) -> Tuple[Optional[str], Optional[float], bool]:
    """
    Send one ICMP Echo Request with a controlled TTL.
    Returns:
        responder_ip, rtt_ms, reached_destination
    """
    recv_sock = create_icmp_receive_socket(timeout)
    send_sock = create_icmp_send_socket(ttl)

    try:
        recv_sock.bind(("", 0))

        identifier = os.getpid() & 0xFFFF
        packet = build_icmp_echo(identifier, sequence, payload)

        start = time.perf_counter()
        send_sock.sendto(packet, (dest_ip, 0))
        _, addr = recv_sock.recvfrom(4096)
        end = time.perf_counter()

        responder_ip = addr[0]
        rtt_ms = (end - start) * 1000.0
        reached = responder_ip == dest_ip

        return responder_ip, rtt_ms, reached

    except socket.timeout:
        return None, None, False

    finally:
        send_sock.close()
        recv_sock.close()


def probe_once_tcp(
    dest_ip: str,
    ttl: int,
    port: int,
    timeout: float,
) -> Tuple[Optional[str], Optional[float], bool]:
    """
    Send one TCP connect-style probe with a controlled TTL.
    Returns:
        responder_ip, rtt_ms, reached_destination
    """
    recv_sock = create_icmp_receive_socket(timeout)
    send_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP)
    send_sock.setsockopt(socket.SOL_IP, socket.IP_TTL, ttl)
    send_sock.settimeout(timeout)

    try:
        recv_sock.bind(("", 0))

        start = time.perf_counter()
        try:
            send_sock.connect((dest_ip, port))
            end = time.perf_counter()
            return dest_ip, (end - start) * 1000.0, True

        except ConnectionRefusedError:
            end = time.perf_counter()
            return dest_ip, (end - start) * 1000.0, True

        except OSError as e:
            if getattr(e, "errno", None) in {errno.ECONNREFUSED}:
                end = time.perf_counter()
                return dest_ip, (end - start) * 1000.0, True
            # Otherwise continue and try to read an ICMP response below.

        _, addr = recv_sock.recvfrom(4096)
        end = time.perf_counter()

        responder_ip = addr[0]
        rtt_ms = (end - start) * 1000.0
        reached = responder_ip == dest_ip

        return responder_ip, rtt_ms, reached

    except socket.timeout:
        return None, None, False

    finally:
        send_sock.close()
        recv_sock.close()


def read_targets(single_destination: Optional[str], input_file: Optional[str]) -> List[Dict[str, Any]]:
    """
    Read targets either from:
      1. a single destination string, or
      2. an input file (.txt or .csv)

    Returns a list of dicts like:
      {"ip": "10.0.3.2", "name": "pc2"}
    or
      {"ip": "8.8.8.8", "name": None}

    Supported formats:
    - TXT: one IP/hostname per line
    - CSV: columns must include 'ip'; optional column 'name'
    """
    if single_destination is not None:
        return [{"ip": single_destination, "name": None}]

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
                targets.append({
                    "ip": line,
                    "name": None,
                })

    elif ext == ".csv":
        with open(input_file, "r", encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)

            if reader.fieldnames is None:
                raise ValueError("CSV file is missing a header row.")

            fieldnames = [name.strip().lower() for name in reader.fieldnames]
            if "ip" not in fieldnames:
                raise ValueError("CSV file must contain an 'ip' column.")

            # Map normalized field names back to original names
            field_map = {name.strip().lower(): name for name in reader.fieldnames}

            ip_key = field_map["ip"]
            name_key = field_map.get("name")

            for row in reader:
                ip_value = row.get(ip_key, "")
                ip_value = ip_value.strip() if ip_value is not None else ""

                if not ip_value:
                    continue

                name_value = None
                if name_key is not None:
                    raw_name = row.get(name_key, "")
                    raw_name = raw_name.strip() if raw_name is not None else ""
                    name_value = raw_name if raw_name else None

                targets.append({
                    "ip": ip_value,
                    "name": name_value,
                })

    else:
        raise ValueError("Unsupported input file type. Use .txt or .csv")

    return targets


def make_probe_result(
    responder_ip: Optional[str],
    rtt_ms: Optional[float],
    reached: bool,
) -> Dict[str, Any]:
    """Package one protocol's result in a JSON-friendly dict."""
    return {
        "responder_ip": responder_ip,
        "rtt_ms": rtt_ms,
        "reached": reached,
        "timeout": responder_ip is None,
    }


def format_probe(proto_name: str, result: Dict[str, Any]) -> str:
    """Format one protocol result for terminal output."""
    ip = result["responder_ip"]
    rtt = result["rtt_ms"]

    if ip is None or rtt is None:
        return f"{proto_name}:*"

    return f"{proto_name}:{ip} {rtt:.2f} ms"

def trace_one_destination(
    destination_ip: str,
    destination_name: Optional[str],
    first_ttl: int,
    max_ttl: int,
    timeout: float,
    port: int,
    payload_size: int,
    num_series: int,
    delay: float,
) -> List[Dict[str, Any]]:
    """
    Run traceroute-style probing for one destination.

    At each TTL:
      - run num_series series
      - each series sends one UDP, one TCP, and one ICMP probe
      - wait 'delay' seconds between consecutive probes

    Returns:
        A list of result dictionaries, one per (ttl, series).
    """
    resolved_ip = resolve_destination(destination_ip)
    payload = b"A" * payload_size
    results: List[Dict[str, Any]] = []
    seq = 1

    display_name = destination_name if destination_name else destination_ip

    print(
        f"\ntraceroute to {display_name} ({resolved_ip}), "
        f"max_ttl={max_ttl}, num_series={num_series}, "
        f"series=[udp,tcp,icmp], port={port}, delay={delay}s"
    )

    destination_reached = False

    for ttl in range(first_ttl, max_ttl + 1):
        for series_id in range(1, num_series + 1):
            udp_ip, udp_rtt, udp_done = probe_once_udp(
                dest_ip=resolved_ip,
                ttl=ttl,
                port=port,
                timeout=timeout,
                payload=payload,
            )
            if delay > 0:
                time.sleep(delay)

            tcp_ip, tcp_rtt, tcp_done = probe_once_tcp(
                dest_ip=resolved_ip,
                ttl=ttl,
                port=port,
                timeout=timeout,
            )
            if delay > 0:
                time.sleep(delay)

            icmp_ip, icmp_rtt, icmp_done = probe_once_icmp(
                dest_ip=resolved_ip,
                ttl=ttl,
                timeout=timeout,
                payload=payload,
                sequence=seq,
            )
            seq += 1

            if delay > 0:
                time.sleep(delay)

            record = {
                "destination": destination_ip,
                "destination_ip": resolved_ip,
                "destination_name": destination_name,
                "ttl": ttl,
                "series": series_id,
                "udp": make_probe_result(udp_ip, udp_rtt, udp_done),
                "tcp": make_probe_result(tcp_ip, tcp_rtt, tcp_done),
                "icmp": make_probe_result(icmp_ip, icmp_rtt, icmp_done),
            }
            results.append(record)

            print(
                f"ttl={ttl:2d} series={series_id:2d}  "
                f"{format_probe('UDP', record['udp']):28s}  "
                f"{format_probe('TCP', record['tcp']):28s}  "
                f"{format_probe('ICMP', record['icmp']):28s}"
            )

            if udp_done or tcp_done or icmp_done:
                destination_reached = True

        if destination_reached:
            print("Destination reached.")
            break

    return results


def write_results_json(results: List[Dict[str, Any]], output_file: str) -> None:
    """Write all traceroute results to a JSON file."""
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Traceroute starter with UDP/TCP/ICMP series, multi-target input, and JSON output"
    )

    # Either positional destination OR --input file
    parser.add_argument(
        "destination",
        nargs="?",
        help="Single destination hostname or IPv4 address",
    )
    parser.add_argument(
        "--input",
        dest="input_file",
        help="Path to text file containing one destination per line",
    )

    parser.add_argument("--first-ttl", type=int, default=1, help="Initial TTL")
    parser.add_argument("--max-ttl", type=int, default=30, help="Maximum TTL")
    parser.add_argument("--timeout", type=float, default=2.0, help="Receive timeout in seconds")
    parser.add_argument("--port", type=int, default=33434, help="Destination port for UDP/TCP")
    parser.add_argument("--payload-size", type=int, default=32, help="Payload size in bytes")
    parser.add_argument("--delay", type=float, default=0.0, help="Delay (seconds) between consecutive probes")
    parser.add_argument("--num-series", type=int, default=1, help="Number of UDP/TCP/ICMP series to send at each TTL")
    parser.add_argument("--output", default="results.json", help="Output JSON file for raw results")

    args = parser.parse_args()

    if args.destination is None and args.input_file is None:
        parser.error("Provide either a destination or --input <file>.")

    if args.destination is not None and args.input_file is not None:
        parser.error("Use either a single destination or --input <file>, not both.")

    return args


def main() -> None:
    args = parse_args()

    targets = read_targets(args.destination, args.input_file)

    all_results: List[Dict[str, Any]] = []

    for target in targets:
        target_ip = target["ip"]
        target_name = target["name"]

        try:
            target_results = trace_one_destination(
                destination_ip=target_ip,
                destination_name=target_name,
                first_ttl=args.first_ttl,
                max_ttl=args.max_ttl,
                timeout=args.timeout,
                port=args.port,
                payload_size=args.payload_size,
                num_series=args.num_series,
                delay=args.delay,
            )

            all_results.extend(target_results)

        except socket.gaierror as e:
            print(f"\nSkipping target '{target_ip}': name resolution failed ({e})")
        except Exception as e:
            print(f"\nSkipping target '{target_ip}': unexpected error ({e})")

    write_results_json(all_results, args.output)
    print(f"\nSaved raw results to: {args.output}")


if __name__ == "__main__":
    main()