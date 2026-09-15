#!/usr/bin/env python3
"""Instance generator for the Set Cover Approximation Competition.

    python3 -m tools.gen --manifest tools/manifest/public.json --out instances
    python3 -m tools.gen --manifest tools/manifest/public.json \
            --instance rnd5k --seed 12345 --salt mine --out mydata

Weighted set cover: a universe of m elements (0..m-1), n sets with integer
costs. Find a minimum-cost sub-collection whose union is the whole universe.

Format of <name>.scp (one set per line after the header):

    m n
    c_0 k_0 e_1 e_2 ... e_k0
    ...
    c_{n-1} k_{n-1} e_1 ... e_k          (elements sorted, no repeats, k >= 1)

Every element belongs to at least two sets, so no set is forced. A
<name>.meta.json is written beside each .scp with the parameters; the proven
lower bound and the reference cover are added later by tools/pipeline.py.

Families
--------
uniform   each set is a uniform random subset of k elements, k in [klo, khi];
          costs uniform in [1, cmax] (cmax = 1 gives a unicost instance). The
          classic OR-Library shape.
geo       m points in the unit square; each set is the points inside a random
          disk whose radius is log-normal, so sets range from a handful of
          points to a whole neighbourhood. Cost grows a little slower than
          size, so big disks are cheaper per point but overlap more.
blocks    elements on a line; a set is a contiguous block plus a few random
          far-away elements. Locally the problem looks like interval covering
          (easy), the stray elements make it hard again.
tail      set sizes are heavy-tailed (bounded Pareto): a few huge sets, many
          tiny ones; cost ~ size^0.9 with noise, so huge sets look like
          bargains to a greedy and are usually not.
"""

import argparse
import hashlib
import json
import math
import random
import sys
from pathlib import Path


def _rng(seed, salt, name):
    h = hashlib.sha256(f"{seed}|{salt}|{name}".encode()).digest()
    return random.Random(int.from_bytes(h[:8], "big"))


def _noisy_cost(size, exponent, rng, spread=(0.7, 1.3)):
    return max(1, int(round(size ** exponent * rng.uniform(*spread))))


def gen_uniform(m, n, rng, klo=10, khi=40, cmax=100):
    cols = []
    for _ in range(n):
        k = rng.randint(klo, min(khi, m))
        cols.append((rng.randint(1, cmax), sorted(rng.sample(range(m), k))))
    return cols


def gen_geo(m, n, rng, rmin=0.01, rmax=0.12, sigma=0.6, cost_exp=0.85):
    pts = [(rng.random(), rng.random()) for _ in range(m)]
    G = 64
    grid = [[[] for _ in range(G)] for _ in range(G)]
    for i, (x, y) in enumerate(pts):
        grid[min(G - 1, int(x * G))][min(G - 1, int(y * G))].append(i)
    cols = []
    while len(cols) < n:
        cx, cy = pts[rng.randrange(m)]
        cx += rng.gauss(0, 0.01)
        cy += rng.gauss(0, 0.01)
        r = min(rmax, max(rmin, math.exp(rng.gauss(math.log(0.03), sigma))))
        members = []
        gx0, gx1 = max(0, int((cx - r) * G)), min(G - 1, int((cx + r) * G))
        gy0, gy1 = max(0, int((cy - r) * G)), min(G - 1, int((cy + r) * G))
        r2 = r * r
        for gx in range(gx0, gx1 + 1):
            for gy in range(gy0, gy1 + 1):
                for i in grid[gx][gy]:
                    dx, dy = pts[i][0] - cx, pts[i][1] - cy
                    if dx * dx + dy * dy <= r2:
                        members.append(i)
        if len(members) < 2:
            continue
        members.sort()
        cols.append((_noisy_cost(len(members), cost_exp, rng), members))
    return cols


def gen_blocks(m, n, rng, llo=5, lhi=60, extra_max=4, cost_exp=0.8):
    cols = []
    for _ in range(n):
        L = rng.randint(llo, lhi)
        s = rng.randrange(0, m - L + 1)
        members = set(range(s, s + L))
        for _ in range(rng.randint(0, extra_max)):
            members.add(rng.randrange(m))
        cols.append((_noisy_cost(L, cost_exp, rng, (0.8, 1.2)) + 1, sorted(members)))
    return cols


def gen_tail(m, n, rng, kmin=3, kmax=None, alpha=1.1, cost_exp=0.9):
    kmax = kmax or max(kmin + 1, m // 4)
    cols = []
    for _ in range(n):
        # bounded Pareto via inverse CDF
        u = rng.random()
        a, b = kmin, kmax
        k = int((-(u * b ** alpha - u * a ** alpha - b ** alpha) / (a ** alpha * b ** alpha)) ** (-1 / alpha))
        k = max(kmin, min(kmax, k))
        cols.append((_noisy_cost(k, cost_exp, rng, (0.6, 1.4)), sorted(rng.sample(range(m), k))))
    return cols


FAMILIES = {"uniform": gen_uniform, "geo": gen_geo, "blocks": gen_blocks, "tail": gen_tail}


def ensure_coverage(m, cols, rng, min_cover=2):
    """Every element in at least `min_cover` sets: add it to random sets."""
    cover = [0] * m
    for _, mem in cols:
        for e in mem:
            cover[e] += 1
    sets_of = [set(mem) for _, mem in cols]
    added = 0
    for e in range(m):
        while cover[e] < min_cover:
            j = rng.randrange(len(cols))
            if e not in sets_of[j]:
                sets_of[j].add(e)
                cover[e] += 1
                added += 1
    if added:
        cols = [(c, sorted(s)) for (c, _), s in zip(cols, sets_of)]
    return cols, added


def write_instance(out_dir, name, m, cols, meta):
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    scp = out_dir / f"{name}.scp"
    nnz = 0
    with open(scp, "w") as f:
        f.write(f"{m} {len(cols)}\n")
        for c, mem in cols:
            nnz += len(mem)
            f.write(f"{c} {len(mem)} " + " ".join(map(str, mem)) + "\n")
    h = hashlib.sha256(scp.read_bytes()).hexdigest()
    meta = dict(meta)
    meta.update(name=name, m=m, n=len(cols), nnz=nnz, total_cost=sum(c for c, _ in cols),
                unicost=all(c == 1 for c, _ in cols), instance_sha256=h)
    (out_dir / f"{name}.meta.json").write_text(json.dumps(meta, indent=2) + "\n")
    return scp


def generate(spec, seed, salt, out_dir):
    name = spec["name"]
    rng = _rng(seed, salt, name)
    fam = FAMILIES[spec["family"]]
    cols = fam(spec["m"], spec["n"], rng, **spec.get("params", {}))
    cols, added = ensure_coverage(spec["m"], cols, rng)
    rng.shuffle(cols)
    meta = {
        "family": spec["family"],
        "category": spec["category"],
        "time_limit_s": spec["time_limit_s"],
        "seed": seed,
        "salt": salt,
        "params": spec.get("params", {}),
        "coverage_fixes": added,
        "description": spec.get("description", ""),
    }
    return write_instance(out_dir, name, spec["m"], cols, meta)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--instance", help="only this instance from the manifest")
    ap.add_argument("--seed", type=int, help="override the manifest seed")
    ap.add_argument("--salt", default=None, help="override the manifest salt")
    args = ap.parse_args(argv)

    manifest = json.loads(args.manifest.read_text())
    seed = args.seed if args.seed is not None else manifest["seed"]
    salt = args.salt if args.salt is not None else manifest.get("salt", "")
    for spec in manifest["instances"]:
        if args.instance and spec["name"] != args.instance:
            continue
        p = generate(spec, seed, salt, args.out)
        print(f"wrote {p}  ({spec['family']}, m={spec['m']}, n={spec['n']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
