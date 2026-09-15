#!/usr/bin/env python3
"""Regression tests for grade.py.

    python3 tools/test_grade.py

  1. the foundation produces a valid cover and scores between 1.0 and 2.0
  2. an invalid cover (uncovered element, duplicate index, junk) scores
     FAIL_RATIO and still counts towards the geometric mean
  3. the time limit, the 4 GB cap and the single-thread rule are enforced
  4. one bad instance does not abort the run
  5. the two categories aggregate correctly
"""

import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GRADE = ROOT / "grade.py"
FOUNDATION = ROOT / "foundation"
INST = ("RANDOM", ROOT / "instances/dev_rnd1k.scp", 5)
INST2 = ("STRUCTURED", ROOT / "instances/dev_geo1k.scp", 5)

PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"   {detail}" if detail else ""))


def run_grade(tmp, solver, extra=(), instances=None):
    ilist = Path(tmp) / "i.txt"
    rows = instances or [INST]
    ilist.write_text("".join(f"{c} {p} {t}\n" for c, p, t in rows))
    out = Path(tmp) / "r.json"
    out.unlink(missing_ok=True)
    r = subprocess.run(
        [sys.executable, str(GRADE), "--solver", str(solver),
         "--instances", str(ilist), "--json", str(out), *extra],
        cwd=ROOT, capture_output=True, text=True)
    if not out.exists():
        print(r.stdout, r.stderr)
        raise SystemExit(f"grade.py produced no result for {solver}")
    return json.loads(out.read_text()), r


def stub(tmp, name, body):
    p = Path(tmp) / name
    p.write_text("#!/usr/bin/env bash\n" + body)
    p.chmod(0o755)
    return p


def main():
    if not FOUNDATION.exists():
        raise SystemExit("build the foundation first: make foundation")
    meta = json.loads((ROOT / "instances/dev_rnd1k.meta.json").read_text())

    with tempfile.TemporaryDirectory() as tmp:
        print("\n[1] foundation as the solver")
        res, _ = run_grade(tmp, FOUNDATION)
        inst = res["instances"][0]
        check("valid cover", inst["note"] == "ok", f"note={inst['note']}")
        check("ratio in [1, 2)", 1.0 <= inst["ratio"] < 2.0, f"ratio={inst['ratio']:.4f}")
        check("cost matches the pipeline's record",
              inst["cost"] == meta["foundation_cost"],
              f"{inst['cost']} vs {meta['foundation_cost']}")
        check("ratio = cost / lower_bound",
              abs(inst["ratio"] - inst["cost"] / inst["lower_bound"]) < 1e-12)
        check("overall geomean equals the single ratio",
              abs(res["overall_geomean"] - inst["ratio"]) < 1e-12)

        print("\n[1b] the source digest binds solver.cpp to the result")
        src = Path(tmp) / "mine.cpp"
        src.write_text("int main(){return 0;}\n")
        res, _ = run_grade(tmp, FOUNDATION, extra=["--source", str(src)])
        check("records the source digest",
              res["solver_source_sha256"] == hashlib.sha256(src.read_bytes()).hexdigest())
        res, _ = run_grade(tmp, FOUNDATION, extra=["--source", str(Path(tmp) / "absent.cpp")])
        check("absent source gives null, not a crash", res["solver_source_sha256"] is None)

        print("\n[2] invalid covers")
        short = stub(tmp, "short.sh", f'"{FOUNDATION}" "$1" "$2" "$3"\nsed -i.bak \'$d\' "$2"\n')
        res, _ = run_grade(tmp, short)
        inst = res["instances"][0]
        check("dropping the last greedy set leaves an element uncovered -> INVALID",
              inst["note"] == "INVALID", f"note={inst['note']} {inst.get('invalid_reason')}")
        check("scores FAIL_RATIO", inst["ratio"] == res["fail_ratio"] == 2.0)
        check("counts towards the geomean", abs(res["overall_geomean"] - 2.0) < 1e-12)

        dup = stub(tmp, "dup.sh", f'"{FOUNDATION}" "$1" "$2" "$3"\nhead -1 "$2" >> "$2"\n')
        res, _ = run_grade(tmp, dup)
        check("a set listed twice is INVALID", res["instances"][0]["note"] == "INVALID",
              res["instances"][0].get("invalid_reason", ""))

        big = stub(tmp, "big.sh", f'"{FOUNDATION}" "$1" "$2" "$3"\nsed -i.bak \'1s/.*/999999999/\' "$2"\n')
        res, _ = run_grade(tmp, big)
        check("out-of-range set index is INVALID", res["instances"][0]["note"] == "INVALID")

        junk = stub(tmp, "junk.sh", 'echo hello > "$2"\n')
        res, _ = run_grade(tmp, junk)
        check("junk output is INVALID", res["instances"][0]["note"] == "INVALID")

        empty = stub(tmp, "empty.sh", ': > "$2"\n')
        res, _ = run_grade(tmp, empty)
        check("empty output is INVALID", res["instances"][0]["note"] == "INVALID")

        none = stub(tmp, "none.sh", 'exit 0\n')
        res, _ = run_grade(tmp, none)
        check("no output file is NOOUTPUT", res["instances"][0]["note"] == "NOOUTPUT")

        every = stub(tmp, "every.sh", 'n=$(head -1 "$1" | cut -d" " -f2); seq 0 $((n-1)) > "$2"\n')
        res, _ = run_grade(tmp, every)
        inst = res["instances"][0]
        check("taking every set is valid", inst["note"] == "ok")
        check("... and costs the total", inst["cost"] == meta["total_cost"])

        print("\n[3] limits")
        res, _ = run_grade(tmp, stub(tmp, "slow.sh", f'"{FOUNDATION}" "$1" "$2" "$3"\nsleep 30\n'),
                           instances=[("RANDOM", INST[1], 2)])
        check("time limit is enforced (valid cover written too late)",
              res["instances"][0]["note"] == "TIMEOUT", f"note={res['instances'][0]['note']}")
        check("a timed-out instance scores FAIL_RATIO", res["instances"][0]["ratio"] == 2.0)

        hog = stub(tmp, "hog.sh",
                   'exec python3 -c "import os; n=5*1024*1024*1024; b=bytearray(n); '
                   'm=memoryview(b); c=os.urandom(1<<20)\n'
                   'for i in range(0, n, 1<<20): m[i:i+(1<<20)]=c\n'
                   'print(n)"\n')
        res, _ = run_grade(tmp, hog, instances=[("RANDOM", INST[1], 60)])
        check("4 GB cap is enforced", res["instances"][0]["note"] in ("MEMORY", "CRASH"),
              f"note={res['instances'][0]['note']}")

        threads = stub(tmp, "threads.sh",
                       'python3 -c "import time; t=time.time()\nwhile time.time()-t<2: pass" &\n'
                       'python3 -c "import time; t=time.time()\nwhile time.time()-t<2: pass" &\n'
                       'python3 -c "import time; t=time.time()\nwhile time.time()-t<2: pass" &\n'
                       f'"{FOUNDATION}" "$1" "$2" "$3"\nwait\n')
        res, _ = run_grade(tmp, threads)
        check("parallel work is detected", res["instances"][0]["note"] == "THREADS",
              f"note={res['instances'][0]['note']}")

        print("\n[4] one bad instance does not abort the run")
        res, _ = run_grade(tmp, stub(tmp, "crash.sh", "exit 3\n"), instances=[INST, INST2])
        check("both instances reported", len(res["instances"]) == 2)
        check("both CRASH", all(i["note"] == "CRASH" for i in res["instances"]))
        check("category means are separate",
              res["random_geomean"] == 2.0 and res["structured_geomean"] == 2.0)

        print("\n[5] two categories aggregate correctly")
        res, _ = run_grade(tmp, FOUNDATION, instances=[INST, INST2])
        r1, r2 = res["instances"][0]["ratio"], res["instances"][1]["ratio"]
        check("random = first, structured = second",
              abs(res["random_geomean"] - r1) < 1e-12 and abs(res["structured_geomean"] - r2) < 1e-12)
        check("overall is the geometric mean",
              abs(res["overall_geomean"] - (r1 * r2) ** 0.5) < 1e-12)

    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        print("failed: " + ", ".join(FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
