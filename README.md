# Kubernetes Equalizer

Evenly redistribute Kubernetes workloads across the nodes you select.  
The script inspects pod placement, plans evictions on overloaded nodes, and optionally issues the eviction calls for you.

## Quick Start

```bash
# install dependencies if needed
pip install -r requirements.txt

# inspect what would happen
python equalizer.py \
  --namespace default \
  --selector app=my-app \
  --dry-run

# apply the plan once you’re comfortable
python equalizer.py \
  --namespace default \
  --selector app=my-app
```

> Always run with `--dry-run` first so you can review the plan before pods are evicted.

## Requirements

- Python 3.7+
- Access to a Kubernetes cluster plus a working kubeconfig (or run in-cluster)
- Python package: `kubernetes`
- Optional: `rich` for the enhanced terminal experience (falls back to plain text otherwise)
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

Run `python equalizer.py --help` to see the same list in your terminal.

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
══════════════════ Equalizer Eviction Plan ✨ ════════════════════
╔═══════════════ Plan Snapshot ═══════════════╗
║ Planned evictions   2                       ║
║ Affected nodes      1                       ║
╚═════════════════════════════════════════════╝

┏━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┳══════════┳━━━━━━┳━━━━━━┳━━━━━━━━┓
┃ Node         ┃ Namespace/Pod               ┃ Priority ┃ Age  ┃ Load ┃ Target ┃
┡━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━╇══════════╇━━━━━━╇━━━━━━╇━━━━━━━━┩
│ node-1       │ default/my-app-6f4d6f...    │ 0        │ 18m  │ 5 → 4│ 3      │
│ node-1       │ default/my-app-6f4d6f...    │ 0        │ 12m  │ 4 → 3│ 3      │
└──────────────┴─────────────────────────────┴──────────┴──────┴──────┴────────┘
Tip: run with --dry-run first to preview the rollout safely.
```

When not in `--dry-run` mode the script reports how many eviction requests were successfully created, highlighting the count in green.

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
