// bound.cpp -- a PROVEN lower bound on the optimal set cover cost.
//
//   bound <instance.scp> [--ub COST] [--iters K] [--json]
//
// Lagrangian relaxation of the covering constraints. For any multipliers
// u >= 0 (one per element),
//
//     L(u) = sum_i u_i + sum_j min(0, c_j - sum_{i in S_j} u_i)
//
// is a lower bound on every cover's cost: take any cover x; its cost is
// sum_j c_j x_j >= sum_j (c_j - sum_{i in S_j} u_i) x_j + sum_i u_i (because
// each element is covered at least once and u_i >= 0), and the right-hand
// side is at least L(u). So the number printed here is valid whatever the
// subgradient ascent below did -- the ascent only makes it tight. Its
// supremum is the LP relaxation value; on weighted random instances that is
// typically within a few percent of the optimum.
//
// Costs are integers, so ceil(L(u)) is also a bound; that is `lower_bound`.

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>
#include <vector>

using std::vector;

static int M = 0, N = 0;
static vector<long long> COST;
static vector<int> START, ELEM;

static void read_instance(const char* path) {
    std::FILE* f = std::fopen(path, "r");
    if (!f) { std::fprintf(stderr, "cannot open %s\n", path); std::exit(1); }
    if (std::fscanf(f, "%d %d", &M, &N) != 2) { std::fprintf(stderr, "bad header\n"); std::exit(1); }
    COST.resize(N); START.resize(N + 1);
    for (int j = 0; j < N; ++j) {
        long long c; int k;
        if (std::fscanf(f, "%lld %d", &c, &k) != 2) { std::fprintf(stderr, "bad set %d\n", j); std::exit(1); }
        COST[j] = c; START[j] = ELEM.size();
        for (int t = 0; t < k; ++t) { int e; if (std::fscanf(f, "%d", &e) != 1) std::exit(1); ELEM.push_back(e); }
    }
    START[N] = ELEM.size();
    std::fclose(f);
}

// Greedy cover, for the UB the step size needs when none is given.
static long long greedy_ub() {
    vector<char> cov(M, 0); int unc = M; long long tot = 0;
    vector<int> gain(N);
    for (int j = 0; j < N; ++j) gain[j] = START[j + 1] - START[j];
    while (unc > 0) {
        int best = -1; double bk = 1e300;
        for (int j = 0; j < N; ++j) {
            if (gain[j] <= 0) continue;
            double k = double(COST[j]) / gain[j];
            if (k < bk) { bk = k; best = j; }
        }
        if (best < 0) break;
        tot += COST[best];
        for (int p = START[best]; p < START[best + 1]; ++p) {
            int e = ELEM[p];
            if (!cov[e]) { cov[e] = 1; --unc; }
        }
        // recompute gains lazily: full recompute every step is O(nnz); fine for a tool
        for (int j = 0; j < N; ++j) {
            int g = 0;
            for (int p = START[j]; p < START[j + 1]; ++p) g += !cov[ELEM[p]];
            gain[j] = g;
        }
    }
    return tot;
}

// Exact evaluation of L(u) and the subgradient g (g_i = 1 - #selected sets containing i).
static double evaluate(const vector<double>& u, vector<int>& g) {
    double L = 0;
    for (int i = 0; i < M; ++i) { L += u[i]; g[i] = 1; }
    for (int j = 0; j < N; ++j) {
        double rc = double(COST[j]);
        for (int p = START[j]; p < START[j + 1]; ++p) rc -= u[ELEM[p]];
        if (rc < 0) {
            L += rc;
            for (int p = START[j]; p < START[j + 1]; ++p) --g[ELEM[p]];
        }
    }
    return L;
}

int main(int argc, char** argv) {
    if (argc < 2) { std::fprintf(stderr, "usage: bound <instance.scp> [--ub COST] [--iters K] [--json]\n"); return 1; }
    read_instance(argv[1]);
    long long ub = -1; int iters = 3000; bool json = false;
    for (int a = 2; a < argc; ++a) {
        if (!std::strcmp(argv[a], "--ub") && a + 1 < argc) ub = std::atoll(argv[++a]);
        else if (!std::strcmp(argv[a], "--iters") && a + 1 < argc) iters = std::atoi(argv[++a]);
        else if (!std::strcmp(argv[a], "--json")) json = true;
    }
    if (ub < 0) ub = greedy_ub();

    // u_i = min over sets containing i of c_j / |S_j|: every reduced cost is
    // then >= 0 and L(u) = sum u_i, a decent start.
    vector<double> u(M, 1e300);
    for (int j = 0; j < N; ++j) {
        double k = double(COST[j]) / (START[j + 1] - START[j]);
        for (int p = START[j]; p < START[j + 1]; ++p) u[ELEM[p]] = std::min(u[ELEM[p]], k);
    }
    for (int i = 0; i < M; ++i) if (u[i] > 1e299) u[i] = 0;

    vector<int> g(M);
    vector<double> bestU = u;
    double best = evaluate(u, g);
    double lambda = 0.1;
    int noimp = 0;
    for (int it = 0; it < iters; ++it) {
        double L = evaluate(u, g);
        if (L > best + 1e-9) { best = L; bestU = u; noimp = 0; }
        else if (++noimp >= 30) { lambda *= 0.5; noimp = 0; }
        if (lambda < 1e-6) break;
        double gn = 0;
        for (int i = 0; i < M; ++i) gn += double(g[i]) * g[i];
        if (gn == 0) break;                              // L(u) is the LP optimum
        double step = lambda * (double(ub) * 1.02 - L) / gn;
        if (step <= 0) step = lambda * 0.01 * std::fabs(L) / gn;
        for (int i = 0; i < M; ++i) u[i] = std::max(0.0, u[i] + step * g[i]);
    }
    // Re-evaluate the best multipliers from scratch so the printed number is
    // exactly L(bestU), whatever floating-point path found it.
    double L = evaluate(bestU, g);
    long long lb = (long long)std::ceil(L - 1e-7);
    if (json) {
        std::printf("{\"lower_bound\": %lld, \"lagrangian\": %.6f, \"ub_used\": %lld, \"iters\": %d}\n", lb, L, ub, iters);
    } else {
        std::printf("lower_bound %lld  (L(u) = %.4f, ub %lld)\n", lb, L, ub);
    }
    return 0;
}
