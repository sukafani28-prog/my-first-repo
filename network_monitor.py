"""Command-line network monitor for speed, latency, and packet loss.

This script samples network interface byte counters to estimate upload/download
throughput, pings a target host to measure latency, and tracks packet loss over
successive probes.
"""

import argparse
import re
import subprocess
import time
from dataclasses import dataclass
from typing import Iterable, Optional, Tuple

PROC_NET_DEV = "/proc/net/dev"
PING_CMD = ["ping", "-c", "1", "-W", "1"]


@dataclass
class InterfaceBytes:
    rx_bytes: int
    tx_bytes: int


@dataclass
class SampleResult:
    timestamp: float
    download_mbps: float
    upload_mbps: float
    latency_ms: Optional[float]
    packet_loss_pct: float


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Monitor network throughput, latency, and packet loss in near real time."
        )
    )
    parser.add_argument(
        "--interface",
        help="Network interface to monitor (defaults to first non-loopback interface)",
    )
    parser.add_argument(
        "--host",
        default="8.8.8.8",
        help="Host to ping for latency and packet-loss measurement",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=1.0,
        help="Seconds between samples",
    )
    parser.add_argument(
        "--count",
        type=int,
        help="Number of samples to take (runs until interrupted if omitted)",
    )
    return parser.parse_args()


def read_interface_bytes(interface: str) -> InterfaceBytes:
    with open(PROC_NET_DEV, "r", encoding="utf-8") as proc_file:
        lines = proc_file.readlines()
    for line in lines[2:]:  # Skip headers
        if ":" not in line:
            continue
        name, stats = (part.strip() for part in line.split(":", maxsplit=1))
        if name != interface:
            continue
        fields = stats.split()
        rx_bytes, tx_bytes = int(fields[0]), int(fields[8])
        return InterfaceBytes(rx_bytes=rx_bytes, tx_bytes=tx_bytes)
    raise ValueError(f"Interface '{interface}' not found in {PROC_NET_DEV}")


def available_interfaces() -> Iterable[str]:
    with open(PROC_NET_DEV, "r", encoding="utf-8") as proc_file:
        lines = proc_file.readlines()
    for line in lines[2:]:
        if ":" not in line:
            continue
        name = line.split(":", maxsplit=1)[0].strip()
        yield name


def default_interface(exclude: Tuple[str, ...] = ("lo",)) -> str:
    for interface in available_interfaces():
        if interface in exclude:
            continue
        return interface
    raise RuntimeError("No usable network interface found")


def measure_latency(host: str) -> Optional[float]:
    command = [*PING_CMD, host]
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        return None
    match = re.search(r"time=([0-9.]+) ms", result.stdout)
    return float(match.group(1)) if match else None


def compute_throughput(
    previous: InterfaceBytes, current: InterfaceBytes, interval: float
) -> Tuple[float, float]:
    rx_delta = max(current.rx_bytes - previous.rx_bytes, 0)
    tx_delta = max(current.tx_bytes - previous.tx_bytes, 0)
    download_mbps = (rx_delta * 8) / (1_000_000 * interval)
    upload_mbps = (tx_delta * 8) / (1_000_000 * interval)
    return download_mbps, upload_mbps


def format_header(interface: str, host: str) -> str:
    return (
        f"Monitoring interface '{interface}' | Host: {host}\n"
        "Time                Download(Mbps)  Upload(Mbps)  Latency(ms)  PacketLoss(%)"
    )


def format_sample(sample: SampleResult) -> str:
    timestamp = time.strftime("%H:%M:%S", time.localtime(sample.timestamp))
    latency = f"{sample.latency_ms:7.2f}" if sample.latency_ms is not None else "   loss"
    loss_pct = f"{sample.packet_loss_pct:12.2f}"
    return (
        f"{timestamp}         {sample.download_mbps:12.2f}  "
        f"{sample.upload_mbps:12.2f}  {latency}  {loss_pct}"
    )


def run_monitor(args: argparse.Namespace) -> None:
    interface = args.interface or default_interface()
    previous_bytes = read_interface_bytes(interface)
    total_pings = 0
    lost_pings = 0

    print(format_header(interface, args.host))

    iterations = args.count if args.count and args.count > 0 else None
    while iterations is None or iterations > 0:
        start = time.time()
        latency = measure_latency(args.host)
        total_pings += 1
        if latency is None:
            lost_pings += 1
        time.sleep(max(args.interval - (time.time() - start), 0))

        current_bytes = read_interface_bytes(interface)
        download_mbps, upload_mbps = compute_throughput(
            previous_bytes, current_bytes, args.interval or 1.0
        )
        previous_bytes = current_bytes

        loss_pct = (lost_pings / total_pings) * 100 if total_pings else 0.0
        sample = SampleResult(
            timestamp=time.time(),
            download_mbps=download_mbps,
            upload_mbps=upload_mbps,
            latency_ms=latency,
            packet_loss_pct=loss_pct,
        )
        print(format_sample(sample))

        if iterations is not None:
            iterations -= 1


def main() -> None:
    args = parse_args()
    run_monitor(args)


if __name__ == "__main__":
    main()
