"""The three Part D experiments: inject a fault, measure detection and recovery.

  1  one Registration replica fails   kubectl delete pod <one>  --grace-period=0 --force
  2  all Registration replicas fail   kubectl delete pod <both> --grace-period=0 --force
  3  database unavailable             kubectl scale statefulset/postgres --replicas=0 (then back to 1)

detection = fault injected -> ready endpoints drop (below 2, or to 0 for 2/3)
recovery  = fault injected -> 2 ready endpoints again
"""
import time

from cluster import kubectl, log, registration_pods, wait_for

BASELINE_S = 5  # steady traffic before and after each fault


def kill_pods(pods):
    log(f"FAULT: force delete {pods if len(pods) > 1 else pods[0]}")
    kubectl("delete", "pod", *pods, "--grace-period=0", "--force", "--wait=false", check=False)


def one_replica(pods, db_outage):
    kill_pods(pods[:1])
    return "One Registration replica fails", (lambda n: n < 2), 60, "endpoint removed"


def all_replicas(pods, db_outage):
    kill_pods(pods)
    return "All Registration replicas fail", (lambda n: n == 0), 60, "all endpoints removed"


def database_down(pods, db_outage):
    log("FAULT: scale statefulset/postgres to 0")
    kubectl("scale", "statefulset/postgres", "--replicas=0")
    return f"Database unavailable ({db_outage} s)", (lambda n: n == 0), 120, "readiness removes all endpoints"


EXPERIMENTS = {1: one_replica, 2: all_replicas, 3: database_down}


def run(n, db_outage):
    """Run experiment n and return its measurements."""
    pods = registration_pods()
    _, ready = wait_for(lambda k: k == 2, 120, "2 ready endpoints before experiment")
    log(f"EXPERIMENT {n} begin, pods={pods}, ready endpoints={ready}")
    time.sleep(BASELINE_S)

    t0 = time.time()
    name, detected, timeout, what = EXPERIMENTS[n](pods, db_outage)
    t_det, _ = wait_for(detected, timeout, what)
    if t_det:
        log(f"DETECTED after {t_det - t0:.2f} s")

    result = {"experiment": n, "name": name}
    if n == 3:  # keep the DB down for the full outage, then bring it back
        time.sleep(max(0, db_outage - (time.time() - t0)))
        log("FAULT REMOVED: scale statefulset/postgres to 1")
        result["db_restored_at_s"] = round(time.time() - t0, 2)
        kubectl("scale", "statefulset/postgres", "--replicas=1")

    t_rec, _ = wait_for(lambda k: k == 2, 300, "2 ready endpoints again")
    if t_rec:
        log(f"RECOVERED (2 ready endpoints) after {t_rec - t0:.2f} s")
    time.sleep(BASELINE_S)

    result.update(t0=t0, t_end=time.time(),
                  detection_time_s=round(t_det - t0, 2) if t_det else None,
                  recovery_time_s=round(t_rec - t0, 2) if t_rec else None)
    return result
