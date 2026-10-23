"""Save the evidence of a run to results/k8s/<timestamp>/."""
import json
import os

from cluster import TIMELINE, kubectl


def write(path, text):
    with open(path, "w") as f:
        f.write(text)


def results_table(stamp, ctx, results):
    lines = [f"# Part D - Kubernetes failure experiments ({stamp}, context `{ctx}`)\n",
             "| # | Injected failure | Detection time | Recovery time | Failed / total requests | Visible outage |",
             "|---|---|---|---|---|---|"]
    for r in results:
        lines.append(f"| {r['experiment']} | {r['name']} | {r['detection_time_s']} s | {r['recovery_time_s']} s | "
                     f"{r['failed']} / {r['requests']} ({r['failed_pct']}%) {r['codes']} | {r['visible_outage_s']} s |")
    lines.append("\nHTTP code 000 = connection failed / timed out (no endpoint answered).")
    return "\n".join(lines) + "\n"


def save(outdir, stamp, ctx, results, load_log):
    """Write every file of the run and return the Markdown table."""
    os.makedirs(outdir, exist_ok=True)
    md = results_table(stamp, ctx, results)
    write(os.path.join(outdir, "load-generator.log"), load_log)
    write(os.path.join(outdir, "events.txt"), kubectl("get", "events", "--sort-by=.lastTimestamp", check=False))
    write(os.path.join(outdir, "pods.txt"), kubectl("get", "pods", "-o", "wide", check=False))
    write(os.path.join(outdir, "timeline.txt"), "".join(f"{t} {m}\n" for t, m in TIMELINE))
    write(os.path.join(outdir, "results.json"), json.dumps({"context": ctx, "results": results}, indent=2))
    write(os.path.join(outdir, "results.md"), md)
    return md
