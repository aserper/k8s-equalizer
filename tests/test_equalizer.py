"""Unit tests for argument parsing, scheduling math, and eviction eligibility."""

import sys

import pytest

import equalizer


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------


def test_parse_args_defaults_to_dry_run(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["equalizer.py"])
    args = equalizer.parse_args()
    assert args.execute is False
    assert args.dry_run is False  # dry-run is the implicit default
    assert args.namespace == "default"


def test_parse_args_accepts_dry_run_flag(monkeypatch):
    monkeypatch.setattr(
        sys, "argv", ["equalizer.py", "--namespace", "demo", "--selector", "app=x", "--dry-run"]
    )
    args = equalizer.parse_args()
    assert args.dry_run is True
    assert args.execute is False
    assert args.namespace == "demo"
    assert args.selector == "app=x"


def test_parse_args_accepts_execute(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["equalizer.py", "--execute"])
    args = equalizer.parse_args()
    assert args.execute is True


def test_parse_args_rejects_dry_run_with_execute(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["equalizer.py", "--dry-run", "--execute"])
    with pytest.raises(SystemExit) as excinfo:
        equalizer.parse_args()
    assert excinfo.value.code == 2


# ---------------------------------------------------------------------------
# Stubs for pod objects
# ---------------------------------------------------------------------------


class StubOwner:
    def __init__(self, kind):
        self.kind = kind


class StubMetadata:
    def __init__(self, name="pod", annotations=None, owner_references=None):
        self.name = name
        self.namespace = "default"
        self.annotations = annotations if annotations is not None else {}
        self.owner_references = owner_references if owner_references is not None else []


class StubSpec:
    def __init__(self, node_name="node-1", priority=None):
        self.node_name = node_name
        self.priority = priority


class StubStatus:
    def __init__(self, phase="Running", start_time=None):
        self.phase = phase
        self.start_time = start_time


class StubPod:
    def __init__(
        self,
        name="pod",
        node_name="node-1",
        phase="Running",
        annotations=None,
        owner_references=None,
        priority=None,
    ):
        self.metadata = StubMetadata(name=name, annotations=annotations, owner_references=owner_references)
        self.spec = StubSpec(node_name=node_name, priority=priority)
        self.status = StubStatus(phase=phase)


# ---------------------------------------------------------------------------
# Eviction eligibility
# ---------------------------------------------------------------------------


def test_is_evictable_accepts_normal_running_pod():
    assert equalizer._is_evictable(StubPod()) is True


def test_is_evictable_skips_daemonset_pods():
    pod = StubPod(owner_references=[StubOwner("DaemonSet")])
    assert equalizer._is_evictable(pod) is False


def test_is_evictable_skips_mirror_pods():
    pod = StubPod(annotations={"kubernetes.io/config.mirror": "abcdef"})
    assert equalizer._is_evictable(pod) is False


def test_is_evictable_skips_pods_marked_unsafe():
    pod = StubPod(annotations={"cluster-autoscaler.kubernetes.io/safe-to-evict": "false"})
    assert equalizer._is_evictable(pod) is False


def test_is_evictable_skips_terminal_phases():
    assert equalizer._is_evictable(StubPod(phase="Succeeded")) is False


# ---------------------------------------------------------------------------
# Scheduling math
# ---------------------------------------------------------------------------


def test_compute_targets_spreads_extra_to_most_loaded_node():
    pods_by_node = {
        "a": [StubPod() for _ in range(5)],
        "b": [StubPod() for _ in range(2)],
    }
    targets = equalizer.compute_targets(["a", "b", "c"], pods_by_node)
    assert targets == {"a": 3, "b": 2, "c": 2}


def test_plan_evicts_only_from_overloaded_nodes():
    heavy = [StubPod(name=f"p{i}", node_name="a") for i in range(4)]
    pods_by_node = {"a": heavy, "b": [StubPod(node_name="b"), StubPod(node_name="b")]}
    targets = {"a": 3, "b": 3}
    plan = equalizer.plan_evictions(["a", "b"], pods_by_node, targets)
    assert [(node, pod.metadata.name) for node, pod in plan] == [("a", "p0")]


def test_group_pods_by_node_buckets_pods():
    pods = [StubPod(name="p1", node_name="a"), StubPod(name="p2", node_name="b")]
    grouped = equalizer.group_pods_by_node(pods)
    assert [p.metadata.name for p in grouped["a"]] == ["p1"]
    assert [p.metadata.name for p in grouped["b"]] == ["p2"]
