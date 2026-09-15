#!/usr/bin/env python3
"""Instructor pipeline: build instances with proven bounds and reference covers.

    python3 tools/pipeline.py build --manifest tools/manifest/public.json \
            --out instances --ref-seconds 300 [--only rnd5k] [--jobs 4]
    python3 tools/pipeline.py check [--dir instances] [--fast]

`build`, per instance:
  1. tools.gen                    -> <name>.scp, <name>.meta.json (with the
                                     file's SHA-256)
  2. ./foundation                 -> foundation_cost (greedy), the baseline
                                     row on the scoreboard
  3. tools/ref/solvers/refsolve   -> <name>.cover (kept in tools/ref/covers/,
                                     gitignored) and ref_cost; also a run on
                                     the students' budget -> ref60_cost
  4. tools/bound/bound --ub ref   -> Lagrangian bound; lower_bound = ceil(L)
  5. sanity: lower_bound <= ref_cost <= foundation_cost, every cover
     re-verifies with grade.py's own checker, foundation/bound < FAIL_RATIO
     (so failing an instance is always worse than running the foundation).
     Any violation FAILS THE BUILD.
  6. merge into <name>.meta.json; write checksums/scored.sha256

`check` re-verifies every instance: digest matches the meta, the reference
cover still has the stored cost, the stored bound is not above it, and
(unless --fast) the bound tool reproduces the stored bound to within 0.1%.
"""

import argparse
import hashlib
import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

from grade import read_instance, verify_cover, sha256_file, FAIL_RATIO  # noqa: E402

BOUND = HERE / "bound" / "bound"
REFSOLVE = HERE / "ref" / "solvers" / "refsolve"
FOUNDATION = ROOT / "foundation"
COVERS = HERE / "ref" / "covers"
CHECKSUMS = ROOT / "checksums"


def run(cmd, **kw):
    r = subprocess.run([str(c) for c in cmd], capture_output=True, text=True, **kw)
    if r.returncode != 0:
        print(r.stdout)
        print(r.stderr)
        raise SystemExit(f"command failed (exit {r.returncode}): {' '.join(str(c) for c in cmd)}")
    return r


def build_one(spec, manifest, out_dir, ref_seconds, bound_iters, reuse_ref=False):
    name = spec["name"]
    scp = out_dir / f"{name}.scp"
    meta_path = out_dir / f"{name}.meta.json"
    log = lambda *a: print(f"[{name}]", *a, flush=True)

    # 1. generate (tools.gen rewrites the meta file; keep the old one for --reuse-ref)
    old_meta = json.loads(meta_path.read_text()) if meta_path.exists() else {}
    run([sys.executable, "-m", "tools.gen", "--manifest", manifest,
         "--instance", name, "--out", out_dir], cwd=ROOT)
    meta = json.loads(meta_path.read_text())
    reusable = reuse_ref and old_meta.get("instance_sha256") == meta.get("instance_sha256")
    if reusable:
        meta.update({k: v for k, v in old_meta.items() if k not in meta})
    m, costs, sets = read_instance(scp)
    COVERS.mkdir(parents=True, exist_ok=True)

    # 2. foundation
    f_cover = COVERS / f"{name}.foundation.cover"
    run([FOUNDATION, scp, f_cover, spec["time_limit_s"]])
    f_cost, why = verify_cover(f_cover, m, costs, sets)
    if f_cost is None:
        raise SystemExit(f"{name}: foundation cover invalid: {why}")
    log(f"foundation {f_cost}")

    # 3. reference (long run) and the same solver on the students' budget
    ref_cover = COVERS / f"{name}.cover"
    secs = ref_seconds if not name.startswith("dev_") else max(10, ref_seconds // 10)
    extra = {}
    if reusable and ref_cover.exists() and "ref_cost" in meta:
        log("reusing existing reference cover")
        ref_cost, why = verify_cover(ref_cover, m, costs, sets)
        extra = {k: meta[k] for k in ("greedy_cost", "lagrangian_cost", "ref_seconds") if k in meta}
    else:
        log(f"refsolve {secs}s ...")
        r = run([REFSOLVE, scp, ref_cover, secs, 1])
        rep = json.loads(r.stdout.strip().splitlines()[-1])
        ref_cost, why = verify_cover(ref_cover, m, costs, sets)
        if ref_cost != rep["cost"]:
            raise SystemExit(f"{name}: refsolve says {rep['cost']}, checker says {ref_cost} ({why})")
        extra = {"greedy_cost": rep["greedy"], "lagrangian_cost": rep["lagrangian"], "ref_seconds": secs}
    if ref_cost is None:
        raise SystemExit(f"{name}: reference cover invalid: {why}")
    log(f"ref {ref_cost}")

    ref60_cover = COVERS / f"{name}.ref60.cover"
    if reusable and ref60_cover.exists() and "ref60_cost" in meta:
        ref60_cost, why = verify_cover(ref60_cover, m, costs, sets)
    else:
        log(f"refsolve {spec['time_limit_s']}s (student budget) ...")
        run([REFSOLVE, scp, ref60_cover, spec["time_limit_s"], 1])
        ref60_cost, why = verify_cover(ref60_cover, m, costs, sets)
    if ref60_cost is None:
        raise SystemExit(f"{name}: 60 s reference cover invalid: {why}")
    extra["ref60_cost"] = ref60_cost
    log(f"ref@{spec['time_limit_s']}s {ref60_cost}")

    # 4. lower bound
    if reusable and "lower_bound" in meta and "lagrangian" in meta:
        lb, L = meta["lower_bound"], meta["lagrangian"]
        log(f"reusing lower_bound {lb}")
    else:
        log("bound ...")
        r = run([BOUND, scp, "--ub", ref_cost, "--iters", bound_iters, "--json"])
        b = json.loads(r.stdout.strip().splitlines()[-1])
        lb, L = int(b["lower_bound"]), b["lagrangian"]
        extra["bound_iters"] = bound_iters
    log(f"lower_bound {lb}  (L(u) {L:.2f}; ref/LB {ref_cost / lb:.4f}, foundation/LB {f_cost / lb:.4f})")

    # 5. sanity
    if not (lb <= ref_cost <= f_cost):
        raise SystemExit(f"{name}: bound/ref/foundation not ordered: {lb} {ref_cost} {f_cost}")
    if f_cost / lb >= FAIL_RATIO:
        raise SystemExit(f"{name}: foundation is {f_cost / lb:.3f}x the bound, not below FAIL_RATIO {FAIL_RATIO} -- "
                         "failing the instance would beat running the foundation; tighten the bound or change the instance")

    # 6. merge
    meta.update(
        lower_bound=lb,
        lagrangian=round(L, 6),
        foundation_cost=f_cost,
        foundation_ratio=round(f_cost / lb, 5),
        ref_cost=ref_cost,
        ref_ratio=round(ref_cost / lb, 5),
        ref60_ratio=round(extra["ref60_cost"] / lb, 5),
        **extra,
    )
    meta_path.write_text(json.dumps(meta, indent=2) + "\n")
    log("meta written")
    return name, meta


def write_checksums(out_dir):
    CHECKSUMS.mkdir(exist_ok=True)
    scored, dev = [], []
    for meta_path in sorted(out_dir.glob("*.meta.json")):
        meta = json.loads(meta_path.read_text())
        line = f"{meta['instance_sha256']}  {meta['name']}.scp\n"
        (dev if meta["name"].startswith("dev_") else scored).append(line)
    (CHECKSUMS / "scored.sha256").write_text("".join(scored))
    (CHECKSUMS / "dev.sha256").write_text("".join(dev))


def cmd_build(args):
    manifest = json.loads(args.manifest.read_text())
    specs = [s for s in manifest["instances"] if not args.only or s["name"] in args.only]
    for exe in (BOUND, REFSOLVE, FOUNDATION):
        if not exe.exists():
            raise SystemExit(f"missing {exe}: run  make foundation tools tools/ref/solvers/refsolve")
    with ThreadPoolExecutor(max_workers=args.jobs) as ex:
        futs = [ex.submit(build_one, s, args.manifest, args.out, args.ref_seconds, args.bound_iters, args.reuse_ref)
                for s in specs]
        results = [f.result() for f in futs]
    write_checksums(args.out)
    print()
    print(f"{'instance':<13}{'m':>7}{'n':>7}{'bound':>9}{'greedy':>9}{'grd/LB':>8}"
          f"{'ref60':>9}{'r60/LB':>8}{'ref300':>9}{'ref/LB':>8}")
    for name, mt in results:
        print(f"{name:<13}{mt['m']:>7}{mt['n']:>7}{mt['lower_bound']:>9}{mt['foundation_cost']:>9}{mt['foundation_ratio']:>8.4f}"
              f"{mt['ref60_cost']:>9}{mt['ref60_ratio']:>8.4f}{mt['ref_cost']:>9}{mt['ref_ratio']:>8.4f}")


def cmd_check(args):
    bad = 0
    for meta_path in sorted(args.dir.glob("*.meta.json")):
        meta = json.loads(meta_path.read_text())
        name = meta["name"]
        scp = args.dir / f"{name}.scp"
        ok = True
        if not scp.exists():
            print(f"{name}: instance file missing (scripts/download.sh?)"); print(f"SKIP {name}"); continue
        if sha256_file(scp) != meta["instance_sha256"]:
            print(f"{name}: digest mismatch"); ok = False
        m, costs, sets = read_instance(scp)
        if m != meta["m"] or len(sets) != meta["n"]:
            print(f"{name}: m/n mismatch"); ok = False
        ref_cover = COVERS / f"{name}.cover"
        if ref_cover.exists() and "ref_cost" in meta:
            c, why = verify_cover(ref_cover, m, costs, sets)
            if c != meta["ref_cost"]:
                print(f"{name}: reference cover {c} != stored {meta['ref_cost']} ({why})"); ok = False
        if "lower_bound" in meta and meta["lower_bound"] > meta.get("ref_cost", 1 << 60):
            print(f"{name}: lower_bound above ref_cost"); ok = False
        if not args.fast and "lagrangian" in meta:
            r = run([BOUND, scp, "--ub", meta["ref_cost"], "--iters", meta.get("bound_iters", 3000), "--json"])
            b = json.loads(r.stdout.strip().splitlines()[-1])
            rel = abs(b["lagrangian"] - meta["lagrangian"]) / max(1.0, meta["lagrangian"])
            if rel > 1e-3:
                print(f"{name}: recomputed bound {b['lagrangian']:.2f} vs stored {meta['lagrangian']:.2f}"); ok = False
        print(f"{'ok   ' if ok else 'BAD  '}{name}")
        bad += not ok
    return 1 if bad else 0


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("--manifest", type=Path, default=HERE / "manifest" / "public.json")
    b.add_argument("--out", type=Path, default=ROOT / "instances")
    b.add_argument("--only", nargs="*")
    b.add_argument("--ref-seconds", type=int, default=300)
    b.add_argument("--bound-iters", type=int, default=6000)
    b.add_argument("--reuse-ref", action="store_true",
                   help="keep existing reference covers and bounds when the instance is unchanged")
    b.add_argument("--jobs", type=int, default=4)
    c = sub.add_parser("check")
    c.add_argument("--dir", type=Path, default=ROOT / "instances")
    c.add_argument("--fast", action="store_true", help="skip recomputing the bounds")
    args = ap.parse_args()
    return cmd_build(args) if args.cmd == "build" else cmd_check(args)


if __name__ == "__main__":
    sys.exit(main() or 0)
