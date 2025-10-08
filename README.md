# Kubernetes Equalizer

Evenly redistribute Kubernetes workloads across the nodes you select.  
The script inspects pod placement, plans evictions on overloaded nodes, and optionally issues the eviction calls for you.

## Quick Start

```bash
# install dependency if needed
pip install kubernetes

# inspect what would happen
python scripts/equalizer.py \
  --namespace default \
  --selector app=my-app \
  --dry-run

# apply the plan once you’re comfortable
python scripts/equalizer.py \
  --namespace default \
  --selector app=my-app
```

> Always run with `--dry-run` first so you can review the plan before pods are evicted.

## Requirements

- Python 3.7+
- Access to a Kubernetes cluster plus a working kubeconfig (or run in-cluster)
- Python package: `kubernetes`
- Permissions to list pods/nodes and create eviction requests in the target namespace

## CLI Reference

| Flag | Description |
| ---- | ----------- |
| `--namespace` | Namespace to inspect (default `default`). |
| `--selector` | Label selector for pods, e.g. `app=my-app`. Targets only bound pods. |
| `--node-selector` | Label selector that limits which nodes participate in balancing. |
| `--kubeconfig` | Path to a kubeconfig file. Defaults to standard kubeconfig loading and falls back to in-cluster config. |
| `--context` | Named context inside the kubeconfig. |
| `--grace-period` | Overrides the pod eviction grace period (seconds). |
| `--max-evictions` | Caps how many pods are evicted in one run. |
| `--dry-run` | Prints the plan without issuing eviction calls. |

Run `python scripts/equalizer.py --help` to see the same list in your terminal.

## How It Works

1. Loads Kubernetes configuration (prefers explicit kubeconfig/context, falls back to defaults).
2. Lists schedulable nodes that match `--node-selector` (if supplied).
3. Lists pods in the namespace that match `--selector`, ignoring ones not yet scheduled.
4. Groups pods by node and computes an even target spread based on current load.
5. For overloaded nodes, picks safe-to-evict pods (skips mirror pods, DaemonSets, pods marked `safe-to-evict=false`, or not `Pending/Running`).
6. Sorts candidates by priority, evicting newer pods first to protect long-running workloads.
7. Prints the plan, warns if pods exist on nodes outside the selected pool, and—unless `--dry-run`—issues eviction API calls.

## Example Output

```
Planned evictions:
  - Evict default/my-app-6f4d6fdfd5-dp7l2 from node-1 (current=5, target=3)
  - Evict default/my-app-6f4d6fdfd5-s8k2m from node-1 (current=5, target=3)
No eviction requests were issued.  # when running with --dry-run
```

When not in `--dry-run` mode the script reports how many eviction requests were successfully created.

## Tips

- Combine `--max-evictions` with cron jobs or automation to pace changes.
- Use distinct label selectors (pods and nodes) to balance only the workloads you care about.
- Watch for warning messages about unschedulable nodes or pods on nodes outside the balancing pool.

## Troubleshooting

- **`ModuleNotFoundError: No module named 'kubernetes'`**  
  Install the client library (`pip install kubernetes`).
- **`No schedulable nodes match the provided filters.`**  
  Adjust or remove `--node-selector`.
- **Evictions failing with API errors**  
  Verify RBAC permissions and cluster policy settings allow eviction requests.
