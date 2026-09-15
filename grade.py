#!/usr/bin/env python3
"""
grade.py -- score a submission for the Set Cover Approximation Competition.

Per instance:
  1. Run the solver once, under the instance's wall-clock limit, the 4 GB
     memory cap and the single-thread rule.
  2. Check the output is a valid cover: a list of set indices in [0, n), no
     index twice, whose union is every element of the universe.
  3. Add up the costs of the chosen sets and divide by the instance's proven
     lower bound:

         ratio = cover_cost / lower_bound        (>= 1.0; lower is better)

     A failed instance (timeout, crash, invalid cover, memory, threads)
     scores FAIL_RATIO = 2.0 and still counts towards the mean.

Aggregate: geometric mean of the ratios per category and overall.
**Lower is better.** 1.000 would mean provably optimal on every instance.

Usage:
  python3 grade.py --solver ./solver --instances instances.txt
  python3 grade.py --solver ./solver --instances instances.txt --json result.json

where instances.txt has one line per instance:
   <category> <instance.scp> <time_limit_seconds>

Categories: RANDOM (uniform random sets) or STRUCTURED (geometric, block and
heavy-tailed families). The `<instance>.meta.json` beside each `.scp` carries
the lower bound; without it the instance cannot be scored.

--- Why a lower bound and not the optimum ------------------------------------

Nobody knows the optimal cover of a 50,000-set instance. What we do have is a
PROVEN lower bound on its cost: the Lagrangian dual value L(u) (tools/bound),
which for any multipliers u >= 0 is at most the cost of every cover, and after
a subgradient ascent sits at the LP relaxation value -- typically a few
percent below the true optimum on weighted instances, more on unicost ones.
So a ratio of 1.05 means "at most 5% above optimal, probably less". The bound
is the same number for everybody on a given instance, so it scales every
student's ratio identically and cancels out of the ranking. What does NOT
cancel is your machine's speed: a faster laptop gets more search done in the
same 60 seconds. The instructor re-runs the top submissions on one machine.
"""

import argparse
import hashlib
import json
import math
import os
import resource
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from collections import defaultdict
from pathlib import Path

RANDOM_CATEGORIES = {"RANDOM"}
STRUCTURED_CATEGORIES = {"STRUCTURED"}
ALL_CATEGORIES = RANDOM_CATEGORIES | STRUCTURED_CATEGORIES

FAIL_RATIO = 2.0                      # a failed instance scores this
MEM_CAP_BYTES = 4 << 30               # the 4 GB rule
TIME_GRACE_S = 1.0                    # process start-up slop past the limit
CPU_WALL_RATIO = 1.4                  # single-thread rule: cpu <= 1.4 * wall

SOLVER_SRC = "solver.cpp"


# --------------------------------------------------------------- running ----

class RunResult:
    def __init__(self, wall, cpu, maxrss, status, stderr=""):
        self.wall = wall
        self.cpu = cpu
        self.maxrss = maxrss
        self.status = status        # ok | timeout | crash | memory | threads | nooutput
        self.stderr = stderr

    @property
    def ok(self):
        return self.status == "ok"


_RSS_UNIT = 1 if sys.platform == "darwin" else 1024   # ru_maxrss: bytes vs KiB


class _Footprint:
    """Live memory footprint of a child on macOS, via proc_pid_rusage.

    macOS refuses to lower RLIMIT_AS, and ru_maxrss under-reports once the
    compressor kicks in, so sample phys_footprint while polling and kill a
    child that crosses the cap. Elsewhere RLIMIT_AS does the job.
    """
    _V2, _SIZE, _OFF = 2, 160, 72

    def __init__(self):
        self.lib = None
        if sys.platform == "darwin":
            try:
                import ctypes, ctypes.util
                self.lib = ctypes.CDLL(ctypes.util.find_library("proc") or "libproc.dylib")
                self.buf = ctypes.create_string_buffer(self._SIZE)
            except OSError:
                self.lib = None

    def sample(self, pid):
        if self.lib is None:
            return 0
        if self.lib.proc_pid_rusage(pid, self._V2, self.buf) != 0:
            return 0
        return int.from_bytes(self.buf.raw[self._OFF:self._OFF + 8], "little")


_FOOTPRINT = _Footprint()


def time_run(binary, instance, out, time_limit, enforce_limits=True):
    """Run the solver once: ./solver <instance> <out> <time_limit>."""
    errfd_r, errfd_w = os.pipe()
    t0 = time.perf_counter()
    pid = os.fork()
    if pid == 0:                                   # child
        try:
            os.close(errfd_r)
            devnull = os.open(os.devnull, os.O_WRONLY)
            os.dup2(devnull, 1)
            os.dup2(errfd_w, 2)
            os.close(errfd_w)
            if enforce_limits:
                try:
                    resource.setrlimit(resource.RLIMIT_AS, (MEM_CAP_BYTES, MEM_CAP_BYTES))
                except (OSError, ValueError):
                    pass                            # macOS: footprint sampling instead
            os.execv(str(binary), [str(binary), str(instance), str(out), str(int(time_limit))])
        except BaseException:
            os._exit(127)
    os.close(errfd_w)

    chunks = []

    def drain():
        with os.fdopen(errfd_r, "rb") as f:
            chunks.append(f.read())

    t = threading.Thread(target=drain, daemon=True)
    t.start()

    deadline = t0 + time_limit + TIME_GRACE_S
    status = rusage = None
    killed = over_cap = False
    peak_footprint = 0
    while True:
        done, st, ru = os.wait4(pid, os.WNOHANG)
        if done:
            status, rusage = st, ru
            break
        now = time.perf_counter()
        if enforce_limits:
            peak_footprint = max(peak_footprint, _FOOTPRINT.sample(pid))
        if now > deadline or (enforce_limits and peak_footprint > MEM_CAP_BYTES):
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            _, _, rusage = os.wait4(pid, 0)
            killed = now > deadline
            over_cap = not killed
            break
        time.sleep(0.002 if now - t0 < 2.0 else 0.02)
    t1 = time.perf_counter()
    t.join(timeout=5)
    err = (chunks[0] if chunks else b"").decode(errors="replace")

    wall = t1 - t0
    if killed:
        return RunResult(wall, 0.0, 0, "timeout", err)
    cpu = rusage.ru_utime + rusage.ru_stime
    rss = max(rusage.ru_maxrss * _RSS_UNIT, peak_footprint)
    if over_cap:
        return RunResult(wall, cpu, rss, "memory", err)
    exited_ok = os.WIFEXITED(status) and os.WEXITSTATUS(status) == 0
    if not exited_ok:
        near_cap = enforce_limits and rss >= MEM_CAP_BYTES * 0.9
        return RunResult(wall, cpu, rss, "memory" if near_cap else "crash", err)
    if enforce_limits and rss > MEM_CAP_BYTES:
        return RunResult(wall, cpu, rss, "memory", err)
    if enforce_limits and wall > 1.0 and cpu > CPU_WALL_RATIO * wall:
        return RunResult(wall, cpu, rss, "threads", err)
    if not Path(out).exists():
        return RunResult(wall, cpu, rss, "nooutput", err)
    return RunResult(wall, cpu, rss, "ok", err)


# ----------------------------------------------------------- the cover ----

def sha256_file(path, chunk=1 << 20):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def read_instance(path):
    """(m, costs, sets) where sets[j] is a list of element ids."""
    with open(path) as f:
        head = f.readline().split()
        m, n = int(head[0]), int(head[1])
        costs, sets = [0] * n, [None] * n
        for j in range(n):
            toks = f.readline().split()
            if len(toks) < 2:
                raise ValueError(f"{path}: set {j} is truncated")
            c, k = int(toks[0]), int(toks[1])
            if len(toks) != 2 + k or k < 1 or c < 0:
                raise ValueError(f"{path}: set {j} is malformed")
            costs[j] = c
            sets[j] = [int(t) for t in toks[2:]]
            for e in sets[j]:
                if e < 0 or e >= m:
                    raise ValueError(f"{path}: set {j} has an element outside [0, m)")
    return m, costs, sets


def verify_cover(cover_path, m, costs, sets):
    """(cost, None) for a valid cover, else (None, reason)."""
    n = len(sets)
    try:
        with open(cover_path) as f:
            toks = f.read().split()
    except OSError as e:
        return None, f"cannot read output: {e}"
    if not toks:
        return None, "empty output"
    chosen = bytearray(n)
    covered = bytearray(m)
    total = 0
    for k, tok in enumerate(toks):
        try:
            j = int(tok)
        except ValueError:
            return None, f"line {k + 1} is not an integer: {tok[:20]!r}"
        if j < 0 or j >= n:
            return None, f"line {k + 1}: set index {j} out of range [0, {n})"
        if chosen[j]:
            return None, f"set {j} is listed twice"
        chosen[j] = 1
        total += costs[j]
        for e in sets[j]:
            covered[e] = 1
    missing = m - sum(covered)
    if missing:
        first = covered.index(0)
        return None, f"{missing} element(s) uncovered, e.g. element {first}"
    return total, None


def load_meta(scp_path):
    p = Path(str(scp_path)[:-4] + ".meta.json") if str(scp_path).endswith(".scp") \
        else Path(str(scp_path) + ".meta.json")
    if not p.exists():
        return None, None
    return json.loads(p.read_text()), p


# --------------------------------------------------------------- scoring ----

def geomean(xs):
    xs = [x for x in xs if x > 0]
    if not xs:
        return 0.0
    return math.exp(sum(math.log(x) for x in xs) / len(xs))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--solver", required=True, type=Path)
    ap.add_argument("--instances", required=True, type=Path)
    ap.add_argument("--no-limits", action="store_true",
                    help="skip the memory and thread checks (debugging only)")
    ap.add_argument("--json", type=Path, help="write machine-readable results here")
    ap.add_argument("--keep-covers", type=Path,
                    help="directory to keep every cover the solver wrote")
    ap.add_argument("--source", type=Path, default=Path(SOLVER_SRC),
                    help="your solver source; its digest goes into --json so "
                         f"the source and the result travel together (default {SOLVER_SRC})")
    args = ap.parse_args()
    args.solver = args.solver.resolve()
    if not args.solver.exists():
        sys.exit(f"solver not found: {args.solver}  (make solver)")

    instances = []
    for line in args.instances.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) != 3 or parts[0] not in ALL_CATEGORIES:
            sys.exit(f"bad instances line (want: <category> <file.scp> <seconds>, "
                     f"category one of {sorted(ALL_CATEGORIES)}): {line}")
        instances.append((parts[0], Path(parts[1]), float(parts[2])))

    missing = [str(p) for _, p, _ in instances if not p.exists()]
    if missing:
        sys.exit("missing instance files:\n  " + "\n  ".join(missing))

    ratios_by_cat = defaultdict(list)
    rows, records = [], []

    if args.keep_covers:
        args.keep_covers.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        for i, (cat, scp, limit) in enumerate(instances):
            meta, meta_path = load_meta(scp)
            name = scp.name[:-4] if scp.name.endswith(".scp") else scp.name
            rec = {"category": cat, "instance": str(scp), "name": name,
                   "time_limit_s": limit}
            if meta is None or "lower_bound" not in meta:
                sys.exit(f"{scp}: no .meta.json with a lower_bound beside it; "
                         "run tools/bound/bound on your own instances first (see README)")
            m, costs, sets = read_instance(scp)
            lb = int(meta["lower_bound"])
            if meta.get("m") is not None and m != meta["m"]:
                sys.exit(f"{scp}: meta says m={meta['m']} but the file has m={m}")
            if meta.get("n") is not None and len(sets) != meta["n"]:
                sys.exit(f"{scp}: meta says n={meta['n']} but the file has {len(sets)} sets")
            if meta.get("instance_sha256") and meta["instance_sha256"] != sha256_file(scp):
                sys.exit(f"{scp}: the file's digest does not match its meta.json -- "
                         "re-download it (scripts/download.sh) or regenerate it")
            rec.update(m=m, n=len(sets), lower_bound=lb,
                       ref_cost=meta.get("ref_cost"),
                       meta_sha256=sha256_file(meta_path))

            out = td / f"{i}.cover"
            r = time_run(args.solver, scp, out, limit,
                         enforce_limits=not args.no_limits)
            if r.stderr.strip():
                rec["stderr"] = r.stderr.strip()[-2000:]

            note, ratio, cost = None, FAIL_RATIO, None
            if not r.ok:
                note = r.status.upper()
            else:
                cost, why = verify_cover(out, m, costs, sets)
                if cost is None:
                    note = "INVALID"
                    rec["invalid_reason"] = why
                else:
                    ratio = cost / lb
                    note = "ok"
                    rec["cover_sha256"] = sha256_file(out)
                    if args.keep_covers:
                        shutil.copy(out, args.keep_covers / f"{name}.cover")

            ratios_by_cat[cat].append(ratio)
            rows.append((cat, name, m, len(sets), limit, r.wall, cost, lb, ratio, note))
            rec.update(cost=cost, ratio=ratio, note=note,
                       wall_s=r.wall, cpu_s=r.cpu, max_rss_bytes=r.maxrss)
            records.append(rec)

    print(f"{'category':<11}{'instance':<11}{'m':>7}{'n':>7}{'limit':>6}{'wall':>7}"
          f"{'cost':>9}{'bound':>9}{'ratio':>8}{'excess':>8}  note")
    print("-" * 100)
    for cat, name, m, n, limit, wall, cost, lb, ratio, note in rows:
        cs = f"{cost:9d}" if cost is not None else f"{'-':>9}"
        ex = f"{(ratio - 1) * 100:7.2f}%" if note == "ok" else f"{'-':>8}"
        print(f"{cat:<11}{name:<11}{m:>7}{n:>7}{limit:6.0f}{wall:7.1f}"
              f"{cs}{lb:9d}{ratio:8.4f}{ex}  {note}")

    rnd = sum((ratios_by_cat[c] for c in RANDOM_CATEGORIES), [])
    stc = sum((ratios_by_cat[c] for c in STRUCTURED_CATEGORIES), [])
    overall = rnd + stc
    print()
    print(f"Random     geomean ratio = {geomean(rnd):.4f}  (n={len(rnd)})")
    print(f"Structured geomean ratio = {geomean(stc):.4f}  (n={len(stc)})")
    print(f"Overall    geomean ratio = {geomean(overall):.4f}  (n={len(overall)})   "
          f"<- your score; lower is better, 1.0000 would be optimal")

    if args.json:
        payload = {
            "competition": "set-cover",
            "solver": str(args.solver),
            "solver_sha256": sha256_file(args.solver),
            "solver_source": str(args.source),
            "solver_source_sha256": (sha256_file(args.source)
                                     if args.source.exists() else None),
            "limits_enforced": not args.no_limits,
            "mem_cap_bytes": MEM_CAP_BYTES,
            "time_grace_s": TIME_GRACE_S,
            "cpu_wall_ratio": CPU_WALL_RATIO,
            "fail_ratio": FAIL_RATIO,
            "instances": records,
            "random_geomean": geomean(rnd),
            "structured_geomean": geomean(stc),
            "overall_geomean": geomean(overall),
        }
        args.json.write_text(json.dumps(payload, indent=2) + "\n")
        print(f"\nwrote {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
