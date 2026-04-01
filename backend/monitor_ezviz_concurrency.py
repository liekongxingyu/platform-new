import argparse
import os
import socket
import sys
import time
from collections import Counter, defaultdict, deque
from dataclasses import dataclass
from pathlib import Path
from typing import Deque, Dict, List, Optional, Set, Tuple

import psutil


DEFAULT_DOMAINS = [
    "open.ys7.com",
    "api.ys7.com",
    "hikopenapi.ys7.com",
    "ajlhvip.ys7.com",
]

DEFAULT_STATES = {"ESTABLISHED", "SYN_SENT", "SYN_RECV", "CLOSE_WAIT"}
DEFAULT_EZVIZ_PORT_MIN = 20000
DEFAULT_EZVIZ_PORT_MAX = 21000


@dataclass
class ConnRow:
    pid: int
    proc_name: str
    laddr: str
    raddr: str
    rhost_hint: str
    status: str


class ConnWindow:
    def __init__(self, seconds: int = 60) -> None:
        self.seconds = seconds
        self.samples: Deque[Tuple[float, int, int]] = deque()
        self.global_peak_total = 0
        self.global_peak_established = 0

    def add(self, now: float, total: int, established: int) -> None:
        self.samples.append((now, total, established))
        if total > self.global_peak_total:
            self.global_peak_total = total
        if established > self.global_peak_established:
            self.global_peak_established = established
        self._trim(now)

    def _trim(self, now: float) -> None:
        cutoff = now - self.seconds
        while self.samples and self.samples[0][0] < cutoff:
            self.samples.popleft()

    def snapshot(self, now: float) -> Dict[str, float]:
        self._trim(now)
        if not self.samples:
            return {
                "window_peak_total": 0,
                "window_peak_established": 0,
                "window_avg_total": 0.0,
                "window_avg_established": 0.0,
                "global_peak_total": float(self.global_peak_total),
                "global_peak_established": float(self.global_peak_established),
            }

        totals = [s[1] for s in self.samples]
        ests = [s[2] for s in self.samples]
        return {
            "window_peak_total": float(max(totals)),
            "window_peak_established": float(max(ests)),
            "window_avg_total": float(sum(totals) / len(totals)),
            "window_avg_established": float(sum(ests) / len(ests)),
            "global_peak_total": float(self.global_peak_total),
            "global_peak_established": float(self.global_peak_established),
        }


class EventWindow:
    def __init__(self, seconds: int = 60) -> None:
        self.seconds = seconds
        self.cache_hit: Deque[float] = deque()
        self.cache_set: Deque[float] = deque()
        self.fetch_error: Deque[float] = deque()

    def add(self, event: str, now: float) -> None:
        if event == "cache_hit":
            self.cache_hit.append(now)
        elif event == "cache_set":
            self.cache_set.append(now)
        elif event == "fetch_error":
            self.fetch_error.append(now)

    def _trim(self, q: Deque[float], now: float) -> None:
        cutoff = now - self.seconds
        while q and q[0] < cutoff:
            q.popleft()

    def snapshot(self, now: float) -> Dict[str, int]:
        self._trim(self.cache_hit, now)
        self._trim(self.cache_set, now)
        self._trim(self.fetch_error, now)
        return {
            "cache_hit": len(self.cache_hit),
            "cache_set": len(self.cache_set),
            "fetch_error": len(self.fetch_error),
        }


class LogTailMonitor:
    def __init__(self, file_path: Optional[Path], window_seconds: int = 60) -> None:
        self.file_path = file_path
        self.window = EventWindow(window_seconds)
        self._offset = 0
        self._inode = None

    def _check_rotate_or_truncate(self) -> None:
        if not self.file_path or not self.file_path.exists():
            return

        stat = self.file_path.stat()
        inode = (stat.st_dev, stat.st_ino)

        if self._inode is None:
            self._inode = inode
            self._offset = stat.st_size
            return

        if inode != self._inode or stat.st_size < self._offset:
            self._inode = inode
            self._offset = 0

    def poll(self, now: float) -> Dict[str, int]:
        if not self.file_path or not self.file_path.exists():
            return self.window.snapshot(now)

        self._check_rotate_or_truncate()

        try:
            with self.file_path.open("r", encoding="utf-8", errors="ignore") as f:
                f.seek(self._offset)
                lines = f.readlines()
                self._offset = f.tell()
        except OSError:
            return self.window.snapshot(now)

        for line in lines:
            if "命中流地址缓存" in line:
                self.window.add("cache_hit", now)
            elif "缓存流地址" in line:
                self.window.add("cache_set", now)
            elif "获取流地址失败" in line:
                self.window.add("fetch_error", now)

        return self.window.snapshot(now)


def resolve_domains(domains: List[str]) -> Tuple[Set[str], Dict[str, Set[str]]]:
    ip_set: Set[str] = set()
    ip_to_domains: Dict[str, Set[str]] = defaultdict(set)

    for domain in domains:
        try:
            infos = socket.getaddrinfo(domain, None)
        except socket.gaierror:
            continue

        for info in infos:
            sockaddr = info[4]
            if not sockaddr:
                continue
            ip = sockaddr[0]
            ip_set.add(ip)
            ip_to_domains[ip].add(domain)

    return ip_set, ip_to_domains


def safe_process_info(pid: int, cache: Dict[int, Tuple[str, str]]) -> Tuple[str, str]:
    if pid <= 0:
        return "unknown", ""

    if pid in cache:
        return cache[pid]

    try:
        proc = psutil.Process(pid)
        name = proc.name() or "unknown"
        cmd = " ".join(proc.cmdline())[:200]
    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
        name = "unknown"
        cmd = ""

    cache[pid] = (name, cmd)
    return name, cmd


def collect_rows(
    target_ips: Set[str],
    ip_to_domains: Dict[str, Set[str]],
    states: Set[str],
    proc_name_filter: Optional[Set[str]],
    ezviz_port_min: int,
    ezviz_port_max: int,
) -> List[ConnRow]:
    rows: List[ConnRow] = []
    proc_cache: Dict[int, Tuple[str, str]] = {}

    try:
        conns = psutil.net_connections(kind="tcp")
    except psutil.AccessDenied:
        print("[WARN] Access denied on net_connections. Try running terminal as Administrator.")
        return rows

    for c in conns:
        if not c.raddr:
            continue

        status = str(c.status or "")
        if status not in states:
            continue

        rip = c.raddr.ip
        rport = c.raddr.port
        ip_match = rip in target_ips
        port_match = ezviz_port_min <= int(rport) <= ezviz_port_max
        if not ip_match and not port_match:
            continue

        pid = int(c.pid or -1)
        proc_name, _ = safe_process_info(pid, proc_cache)

        if proc_name_filter and proc_name.lower() not in proc_name_filter and pid > 0:
            continue

        laddr = f"{c.laddr.ip}:{c.laddr.port}" if c.laddr else "-"
        raddr = f"{rip}:{rport}"
        if ip_match:
            domain_hint = ",".join(sorted(ip_to_domains.get(rip, []))) or "(resolved-ip)"
        else:
            domain_hint = f"(port-match:{rport})"

        rows.append(
            ConnRow(
                pid=pid,
                proc_name=proc_name,
                laddr=laddr,
                raddr=raddr,
                rhost_hint=domain_hint,
                status=status,
            )
        )

    return rows


def clear_screen() -> None:
    os.system("cls" if os.name == "nt" else "clear")


def print_dashboard(
    rows: List[ConnRow],
    domains: List[str],
    target_ips: Set[str],
    log_metrics: Dict[str, int],
    interval: float,
    top_n: int,
    window_seconds: int,
    ezviz_port_min: int,
    ezviz_port_max: int,
    conn_metrics: Dict[str, float],
) -> None:
    now_str = time.strftime("%Y-%m-%d %H:%M:%S")
    per_proc = Counter((r.pid, r.proc_name) for r in rows)
    per_remote = Counter(r.raddr for r in rows)

    print(f"[EZVIZ Concurrency Monitor] {now_str}")
    print("=" * 90)
    print(f"domains: {', '.join(domains) if domains else '(disabled, port-only mode)'}")
    print(f"resolved IP count: {len(target_ips)}")
    print(f"fallback port range: {ezviz_port_min}-{ezviz_port_max}")
    print(f"matching TCP conns: {len(rows)} | unique processes: {len(per_proc)} | refresh: {interval:.1f}s")
    print(
        f"conn window {window_seconds}s: "
        f"peak_total={int(conn_metrics['window_peak_total'])} "
        f"peak_established={int(conn_metrics['window_peak_established'])} "
        f"avg_total={conn_metrics['window_avg_total']:.2f} "
        f"avg_established={conn_metrics['window_avg_established']:.2f}"
    )
    print(
        f"session peaks: total={int(conn_metrics['global_peak_total'])} "
        f"established={int(conn_metrics['global_peak_established'])}"
    )
    print(
        f"backend log {window_seconds}s: "
        f"cache_hit={log_metrics['cache_hit']}  cache_set={log_metrics['cache_set']}  fetch_error={log_metrics['fetch_error']}"
    )

    print("\nTop processes by connection count:")
    if per_proc:
        for (pid, name), cnt in per_proc.most_common(top_n):
            print(f"  PID {pid:<6} {name:<22} {cnt}")
    else:
        print("  (none)")

    print("\nTop remote endpoints:")
    if per_remote:
        for remote, cnt in per_remote.most_common(top_n):
            print(f"  {remote:<28} {cnt}")
    else:
        print("  (none)")

    print("\nActive connection details:")
    if rows:
        print("  PID     Process                 Status       Local -> Remote                       HostHint")
        print("  " + "-" * 84)
        for r in rows[: max(top_n * 4, 20)]:
            print(
                f"  {r.pid:<7} {r.proc_name[:22]:<22} {r.status:<11} {r.laddr:<22} -> {r.raddr:<22} {r.rhost_hint}"
            )
    else:
        print("  (none)")

    print("\nTips:")
    print("  1) If browser process count is high, close duplicate tabs/windows and keep one preview only.")
    print("  2) If multiple python/node/ffmpeg processes appear, check duplicated backend/frontend services.")
    print("  3) If cache_hit is low but cache_set/fetch_error is high, stream URL cache may be bypassed.")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Realtime monitor for EZVIZ related concurrent connections")
    parser.add_argument("--interval", type=float, default=2.0, help="Refresh interval in seconds")
    parser.add_argument("--resolve-every", type=int, default=30, help="Re-resolve domains every N seconds")
    parser.add_argument("--top", type=int, default=8, help="Top rows to show for process/endpoint summary")
    parser.add_argument(
        "--domains",
        nargs="*",
        default=DEFAULT_DOMAINS,
        help="Domain list to monitor (default: open/api/hikopenapi/ajlhvip.ys7.com)",
    )
    parser.add_argument(
        "--states",
        nargs="*",
        default=sorted(DEFAULT_STATES),
        help="TCP states to include (default: ESTABLISHED SYN_SENT SYN_RECV CLOSE_WAIT)",
    )
    parser.add_argument(
        "--proc-names",
        nargs="*",
        default=[],
        help="Optional process-name filter, e.g. chrome msedge python ffmpeg node",
    )
    parser.add_argument(
        "--backend-log",
        default="logs/smart_helmet.log",
        help="Backend log path for cache hit/set/error counting",
    )
    parser.add_argument(
        "--window-seconds",
        type=int,
        default=60,
        help="Rolling time window for backend log metrics",
    )
    parser.add_argument(
        "--ezviz-port-min",
        type=int,
        default=DEFAULT_EZVIZ_PORT_MIN,
        help="Fallback minimum remote port for EZVIZ matching",
    )
    parser.add_argument(
        "--ezviz-port-max",
        type=int,
        default=DEFAULT_EZVIZ_PORT_MAX,
        help="Fallback maximum remote port for EZVIZ matching",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    domains = list(dict.fromkeys([d.strip() for d in args.domains if d.strip()]))

    proc_name_filter = {n.lower() for n in args.proc_names if n.strip()} or None
    states = {s.upper() for s in args.states if s.strip()}

    log_path = Path(args.backend_log)
    if not log_path.is_absolute():
        log_path = (Path(__file__).resolve().parent / log_path).resolve()

    log_monitor = LogTailMonitor(log_path, window_seconds=max(10, int(args.window_seconds)))
    conn_window = ConnWindow(seconds=max(10, int(args.window_seconds)))

    target_ips, ip_to_domains = resolve_domains(domains)
    last_resolve_at = time.time()

    while True:
        now = time.time()
        if now - last_resolve_at >= max(5, int(args.resolve_every)):
            target_ips, ip_to_domains = resolve_domains(domains)
            last_resolve_at = now

        rows = collect_rows(
            target_ips,
            ip_to_domains,
            states,
            proc_name_filter,
            ezviz_port_min=int(args.ezviz_port_min),
            ezviz_port_max=int(args.ezviz_port_max),
        )
        log_metrics = log_monitor.poll(now)
        established = sum(1 for r in rows if r.status == "ESTABLISHED")
        conn_window.add(now, total=len(rows), established=established)
        conn_metrics = conn_window.snapshot(now)

        clear_screen()
        print_dashboard(
            rows=rows,
            domains=domains,
            target_ips=target_ips,
            log_metrics=log_metrics,
            interval=float(args.interval),
            top_n=int(args.top),
            window_seconds=max(10, int(args.window_seconds)),
            ezviz_port_min=int(args.ezviz_port_min),
            ezviz_port_max=int(args.ezviz_port_max),
            conn_metrics=conn_metrics,
        )

        try:
            time.sleep(max(0.5, float(args.interval)))
        except KeyboardInterrupt:
            print("\nStopped.")
            return 0


if __name__ == "__main__":
    sys.exit(main())
