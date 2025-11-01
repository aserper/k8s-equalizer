#!/usr/bin/env python3
"""Evenly redistribute Kubernetes workloads across available nodes.

Example:
    python scripts/equalizer.py --namespace default --selector app=my-app --dry-run

Run with --dry-run first to review the eviction plan before applying it.
"""

import argparse
import datetime
import sys
from collections import defaultdict
from typing import Dict, List, Optional, Sequence

try:
    from kubernetes import client, config
except ImportError as _IMPORT_ERROR:  # pragma: no cover - runtime guardrail
    client = None  # type: ignore[assignment]
    config = None  # type: ignore[assignment]
else:
    _IMPORT_ERROR = None

try:
    from rich import box
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text
    from rich.theme import Theme
except ImportError:  # pragma: no cover - optional dependency
    Console = None  # type: ignore[assignment]
    Panel = None  # type: ignore[assignment]
    Table = None  # type: ignore[assignment]
    Text = None  # type: ignore[assignment]
    Theme = None  # type: ignore[assignment]
    box = None  # type: ignore[assignment]
    _HAS_RICH = False
else:
    _HAS_RICH = True


if _HAS_RICH and Console is not None and Theme is not None:
    _THEME = Theme(
        {
            "title": "bold cyan",
            "success": "bold green",
            "warning": "bold yellow",
            "error": "bold red",
            "muted": "dim",
            "value": "bold white",
        }
    )
    STDOUT_CONSOLE = Console(theme=_THEME)
    STDERR_CONSOLE = Console(theme=_THEME, stderr=True)
else:  # pragma: no cover - fallback path
    STDOUT_CONSOLE = None
    STDERR_CONSOLE = None
    _HAS_RICH = False


def _print_info(message: str) -> None:
    if STDOUT_CONSOLE is not None:
        STDOUT_CONSOLE.print(message)
    else:
        print(message)


def _print_success(message: str) -> None:
    if STDOUT_CONSOLE is not None:
        STDOUT_CONSOLE.print(f"[success]{message}[/success]")
    else:
        print(message)


def _print_warning(message: str) -> None:
    if STDERR_CONSOLE is not None:
        STDERR_CONSOLE.print(f"[warning]{message}[/warning]")
    else:
        print(message, file=sys.stderr)


def _print_error(message: str) -> None:
    if STDERR_CONSOLE is not None:
        STDERR_CONSOLE.print(f"[error]{message}[/error]")
    else:
        print(message, file=sys.stderr)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Evict pods from overloaded nodes so that workloads are evenly spread."
    )
    parser.add_argument(
        "--namespace",
        default="default",
        help="Namespace to inspect (default: %(default)s).",
    )
    parser.add_argument(
        "--selector",
        default=None,
        help="Label selector used to filter pods, e.g. app=my-app.",
    )
    parser.add_argument(
        "--node-selector",
        default=None,
        help="Label selector to limit which nodes participate in balancing.",
    )
    parser.add_argument(
        "--kubeconfig",
        default=None,
        help="Explicit path to kubeconfig; falls back to in-cluster or default config.",
    )
    parser.add_argument(
        "--context",
        default=None,
        help="Named context inside kubeconfig.",
    )
    parser.add_argument(
        "--grace-period",
        type=int,
        default=None,
        help="Optional grace period (seconds) passed to the eviction API.",
    )
    parser.add_argument(
        "--max-evictions",
        type=int,
        default=None,
        help="Upper bound on number of evictions performed in a single run.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Plan balancing actions without evicting pods.",
    )
    return parser.parse_args()


def load_client(kubeconfig: Optional[str], context: Optional[str]):
    if client is None or config is None:
        raise SystemExit(
            "This script requires the kubernetes Python package. Install it with\n"
            "  pip install kubernetes\n"
            f"Original error: {_IMPORT_ERROR}"
        )

    try:
        if kubeconfig or context:
            config.load_kube_config(config_file=kubeconfig, context=context)
        else:
            config.load_kube_config()
    except config.config_exception.ConfigException:
        config.load_incluster_config()

    return client


def list_schedulable_nodes(
    core_api,
    selector: Optional[str],
) -> Sequence[str]:
    nodes = core_api.list_node(label_selector=selector).items
    filtered = [
        node.metadata.name
        for node in nodes
        if not getattr(node.spec, "unschedulable", False)
    ]
    if not filtered:
        raise SystemExit("No schedulable nodes match the provided filters.")
    return sorted(filtered)


def list_target_pods(core_api, namespace: str, selector: Optional[str]):
    pods = core_api.list_namespaced_pod(
        namespace=namespace,
        label_selector=selector,
    ).items
    return [pod for pod in pods if pod.spec.node_name]


def group_pods_by_node(pods) -> Dict[str, List]:
    grouped: Dict[str, List] = defaultdict(list)
    for pod in pods:
        grouped[pod.spec.node_name].append(pod)
    return grouped


def _is_managed_by_daemonset(pod) -> bool:
    for owner in pod.metadata.owner_references or []:
        if owner.kind == "DaemonSet":
            return True
    return False


def _is_evictable(pod) -> bool:
    annotations = pod.metadata.annotations or {}
    if annotations.get("kubernetes.io/config.mirror"):
        return False
    if annotations.get("cluster-autoscaler.kubernetes.io/safe-to-evict") == "false":
        return False
    if _is_managed_by_daemonset(pod):
        return False
    if pod.status.phase not in {"Pending", "Running"}:
        return False
    return True


def _pod_sort_key(pod):
    priority = pod.spec.priority if pod.spec.priority is not None else 0
    # Evict the newest pods first to reduce impact on long-running workloads.
    start_time = pod.status.start_time
    if start_time is None:
        start_ts = float("inf")
    else:
        if start_time.tzinfo is None:
            start_time = start_time.replace(tzinfo=datetime.timezone.utc)
        start_ts = start_time.timestamp()
    return (priority, -start_ts)


def _format_pod_age(pod) -> str:
    start_time = getattr(pod.status, "start_time", None)
    if start_time is None:
        return "n/a"
    if start_time.tzinfo is None:
        start_time = start_time.replace(tzinfo=datetime.timezone.utc)
    delta = datetime.datetime.now(datetime.timezone.utc) - start_time
    seconds_total = int(delta.total_seconds())
    if seconds_total <= 0:
        return "0s"
    minutes, seconds = divmod(seconds_total, 60)
    hours, minutes = divmod(minutes, 60)
    days, hours = divmod(hours, 24)
    if days:
        return f"{days}d{hours}h"
    if hours:
        return f"{hours}h{minutes}m"
    if minutes:
        return f"{minutes}m{seconds}s"
    return f"{seconds}s"


def compute_targets(
    nodes: Sequence[str],
    pods_by_node: Dict[str, Sequence],
) -> Dict[str, int]:
    counts = {node: len(pods_by_node.get(node, ())) for node in nodes}
    total = sum(counts.values())
    per_node, extra = divmod(total, len(nodes))
    ranked_nodes = sorted(nodes, key=lambda name: counts[name], reverse=True)
    targets = {node: per_node for node in nodes}
    for idx in range(extra):
        targets[ranked_nodes[idx]] += 1
    return targets


def plan_evictions(
    nodes: Sequence[str],
    pods_by_node: Dict[str, Sequence],
    targets: Dict[str, int],
) -> List:
    plan = []
    for node in nodes:
        overload = len(pods_by_node.get(node, ())) - targets[node]
        if overload <= 0:
            continue
        candidates = sorted(
            [pod for pod in pods_by_node.get(node, []) if _is_evictable(pod)],
            key=_pod_sort_key,
        )
        if len(candidates) < overload:
            shortage = overload - len(candidates)
            _print_warning(
                f"Node {node} is {overload} pods over target "
                f"but only {len(candidates)} are safe to evict; "
                f"{shortage} pod(s) will remain imbalanced."
            )
            overload = len(candidates)
        plan.extend((node, candidates[idx]) for idx in range(overload))
    return plan


def print_plan(plan, targets, pods_by_node):
    if not plan:
        if _HAS_RICH and STDOUT_CONSOLE is not None and Panel is not None and Text is not None:
            STDOUT_CONSOLE.rule(Text("Equalizer Status", style="title"))
            STDOUT_CONSOLE.print(
                Panel.fit(
                    Text(
                        "Workload distribution already balanced across nodes.",
                        style="success",
                    ),
                    border_style="success",
                    title="Balanced",
                )
            )
        else:
            print("Workload distribution already balanced across nodes.")
        return

    if _HAS_RICH and all(
        component is not None
        for component in (STDOUT_CONSOLE, Table, Panel, Text, box)
    ):
        _render_rich_plan(plan, targets, pods_by_node)
        return

    print("Planned evictions:")
    for node, pod in plan:
        current = len(pods_by_node.get(node, ()))
        target = targets[node]
        print(
            f"  - Evict {pod.metadata.namespace}/{pod.metadata.name} "
            f"from {node} (current={current}, target={target})"
        )


def _render_rich_plan(plan, targets, pods_by_node) -> None:
    assert STDOUT_CONSOLE is not None  # for mypy
    assert Table is not None and Panel is not None and Text is not None and box is not None

    total_evictions = len(plan)
    affected_nodes = sorted({node for node, _ in plan})

    STDOUT_CONSOLE.rule(Text("Equalizer Eviction Plan ✨", style="title"))

    overview = Table.grid(padding=(0, 2))
    overview.add_column(style="muted", justify="right")
    overview.add_column(style="value")
    overview.add_row("Planned evictions", str(total_evictions))
    overview.add_row("Affected nodes", str(len(affected_nodes)))

    STDOUT_CONSOLE.print(
        Panel.fit(
            overview,
            title="Plan Snapshot",
            border_style="title",
        )
    )

    table = Table(
        title="Eviction Rollout",
        box=box.ROUNDED,
        show_header=True,
        header_style="title",
        expand=True,
    )
    table.add_column("Node", style="value", no_wrap=True)
    table.add_column("Namespace/Pod", style="value", no_wrap=False)
    table.add_column("Priority", justify="right", style="muted", no_wrap=True)
    table.add_column("Age", style="muted", no_wrap=True)
    table.add_column("Load", style="value", no_wrap=True)
    table.add_column("Target", justify="right", style="value", no_wrap=True)

    seen = defaultdict(int)
    for node, pod in plan:
        seen[node] += 1
        current_total = len(pods_by_node.get(node, ()))
        before = current_total - (seen[node] - 1)
        after = max(before - 1, 0)
        target = targets[node]
        priority = getattr(pod.spec, "priority", None)
        priority_text = "-" if priority is None else str(priority)
        age_text = _format_pod_age(pod)
        load_text = f"{before} → {after}"
        target_style = "success" if after <= target else "warning"
        target_text = f"[{target_style}]{target}[/]" if target_style else str(target)
        table.add_row(
            f"[value]{node}[/value]",
            f"{pod.metadata.namespace}/{pod.metadata.name}",
            priority_text,
            age_text,
            load_text,
            target_text,
        )

    STDOUT_CONSOLE.print(table)
    STDOUT_CONSOLE.print(
        Text("Tip: run with --dry-run first to preview the rollout safely.", style="muted")
    )


def warn_on_unlisted_nodes(pods_by_node: Dict[str, Sequence], nodes: Sequence[str]) -> None:
    unknown = sorted(set(pods_by_node.keys()) - set(nodes))
    if unknown:
        joined = ", ".join(unknown)
        _print_warning(
            "Found pods scheduled on nodes outside the selected pool: "
            f"{joined}. They will not be adjusted."
        )


def execute_plan(
    plan,
    eviction_api,
    client_module,
    dry_run: bool,
    grace_period: Optional[int],
    max_evictions: Optional[int],
) -> int:
    if not plan:
        return 0

    performed = 0
    for node, pod in plan:
        if max_evictions is not None and performed >= max_evictions:
            _print_warning("Reached --max-evictions limit, stopping.")
            break
        if dry_run:
            continue
        body = create_eviction_body(pod, grace_period)
        try:
            eviction_api.create_namespaced_pod_eviction(
                name=pod.metadata.name,
                namespace=pod.metadata.namespace,
                body=body,
            )
            performed += 1
        except client_module.exceptions.ApiException as exc:  # pragma: no cover
            _print_error(
                f"Failed to evict {pod.metadata.namespace}/{pod.metadata.name}: {exc}"
            )
    return performed


def create_eviction_body(pod, grace_period: Optional[int]):
    delete_opts = client.V1DeleteOptions(
        grace_period_seconds=grace_period,
    )
    return client.V1Eviction(
        metadata=client.V1ObjectMeta(
            name=pod.metadata.name,
            namespace=pod.metadata.namespace,
        ),
        delete_options=delete_opts,
    )


def main():
    args = parse_args()
    client = load_client(args.kubeconfig, args.context)
    core_api = client.CoreV1Api()
    nodes = list_schedulable_nodes(core_api, args.node_selector)
    pods = list_target_pods(core_api, args.namespace, args.selector)
    pods_by_node = group_pods_by_node(pods)
    warn_on_unlisted_nodes(pods_by_node, nodes)
    targets = compute_targets(nodes, pods_by_node)
    plan = plan_evictions(nodes, pods_by_node, targets)
    print_plan(plan, targets, pods_by_node)
    if args.dry_run:
        return 0
    eviction_api = core_api
    policy_api_cls = getattr(client, "PolicyV1Api", None)
    if policy_api_cls is not None:
        try:
            candidate = policy_api_cls()
            if hasattr(candidate, "create_namespaced_pod_eviction"):
                eviction_api = candidate
        except Exception:  # pragma: no cover - defensive fallback
            pass
    evicted = execute_plan(
        plan=plan,
        eviction_api=eviction_api,
        client_module=client,
        dry_run=args.dry_run,
        grace_period=args.grace_period,
        max_evictions=args.max_evictions,
    )
    if evicted:
        _print_success(f"Successfully issued {evicted} eviction request(s).")
    else:
        _print_info("No eviction requests were issued.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
