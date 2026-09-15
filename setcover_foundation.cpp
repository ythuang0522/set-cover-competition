// setcover_foundation.cpp
//
// Foundation solver for the Set Cover Approximation Competition: the greedy
// algorithm of slide 13 (*Set Cover*), GREEDY_SET_COVER, generalised to set
// costs in the standard way -- at every step take the set that covers the
// most still-uncovered elements PER UNIT COST. With unit costs it is exactly
// the slide. Its cover is within a factor H(max |S|) = O(log n) of optimal
// (slides 15-25). Students start from this file and make its covers CHEAPER.
//
// Usage:
//     ./solver <instance.scp> <output.cover> <time_limit_seconds>
//
// The third argument is the wall-clock budget the grader will enforce. This
// foundation ignores it (it finishes in about a second); a better solver
// keeps improving its cover until the budget is nearly used up.
//
// File formats:
//
//   <instance.scp>
//     m n                          (elements 0..m-1, number of sets)
//     c_0 k_0 e_1 ... e_k0         (cost, size, the elements -- one set per line)
//     ...
//     c_{n-1} k_{n-1} e_1 ... e_k
//
//   <output.cover>
//     j_1
//     j_2
//     ...                          (indices of the chosen sets, 0 <= j < n,
//                                   one per line, each at most once)
//
// A cover is valid if the chosen sets together contain every element; its
// cost is the sum of their costs. The grader checks both exactly in 64-bit
// integers.

#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <queue>
#include <utility>
#include <vector>

using std::vector;

static int32_t M = 0, N = 0;
static vector<int64_t> COST;                 // cost of set j
static vector<int32_t> START;                // set j is ELEM[START[j] .. START[j+1])
static vector<int32_t> ELEM;

static void read_instance(const char* path) {
    std::FILE* f = std::fopen(path, "r");
    if (!f) { std::fprintf(stderr, "cannot open instance: %s\n", path); std::exit(1); }
    if (std::fscanf(f, "%d %d", &M, &N) != 2 || M <= 0 || N <= 0) {
        std::fprintf(stderr, "bad instance header\n"); std::exit(1);
    }
    COST.resize(N); START.resize(N + 1);
    for (int32_t j = 0; j < N; ++j) {
        long long c; int32_t k;
        if (std::fscanf(f, "%lld %d", &c, &k) != 2 || c < 0 || k < 1) {
            std::fprintf(stderr, "bad set header at set %d\n", j); std::exit(1);
        }
        COST[j] = c; START[j] = ELEM.size();
        for (int32_t t = 0; t < k; ++t) {
            int32_t e;
            if (std::fscanf(f, "%d", &e) != 1 || e < 0 || e >= M) {
                std::fprintf(stderr, "bad element in set %d\n", j); std::exit(1);
            }
            ELEM.push_back(e);
        }
    }
    START[N] = ELEM.size();
    std::fclose(f);
}

// Greedy set cover, O(total size * log n).
//
// The textbook loop re-scans every set at every step to find the one with
// the most new elements per unit cost, which is O(n) per step. Instead keep
// the sets in a min-heap keyed by cost / (new elements). A set's true key can
// only get WORSE as elements get covered, so a stale key at the top of the
// heap is an optimistic estimate: pop it, recompute, and if it is still the
// best (no worse than the next entry) take it, otherwise push it back with
// its true key. Each set is re-pushed at most once per element it loses.
static vector<int32_t> greedy() {
    vector<char> covered(M, 0);
    int32_t uncovered = M;
    typedef std::pair<double, int32_t> Entry;             // (key, set)
    std::priority_queue<Entry, vector<Entry>, std::greater<Entry>> heap;
    for (int32_t j = 0; j < N; ++j)
        heap.push(Entry(double(COST[j]) / double(START[j + 1] - START[j]), j));

    vector<int32_t> chosen;
    while (uncovered > 0 && !heap.empty()) {
        Entry top = heap.top(); heap.pop();
        int32_t j = top.second;
        int32_t gain = 0;
        for (int32_t p = START[j]; p < START[j + 1]; ++p) gain += !covered[ELEM[p]];
        if (gain == 0) continue;                           // covers nothing new any more
        double key = double(COST[j]) / double(gain);
        if (!heap.empty() && key > heap.top().first) {     // stale: re-insert with the true key
            heap.push(Entry(key, j));
            continue;
        }
        chosen.push_back(j);
        for (int32_t p = START[j]; p < START[j + 1]; ++p) {
            if (!covered[ELEM[p]]) { covered[ELEM[p]] = 1; --uncovered; }
        }
    }
    return chosen;
}

static void write_cover(const char* path, const vector<int32_t>& chosen) {
    std::FILE* f = std::fopen(path, "w");
    if (!f) { std::fprintf(stderr, "cannot open output: %s\n", path); std::exit(1); }
    for (int32_t j : chosen) std::fprintf(f, "%d\n", j);
    std::fclose(f);
}

int main(int argc, char** argv) {
    if (argc != 4) {
        std::fprintf(stderr, "usage: %s <instance.scp> <output.cover> <time_limit_seconds>\n", argv[0]);
        return 1;
    }
    read_instance(argv[1]);

    // GREEDY_SET_COVER (slide 13), with cost per newly covered element as the
    // selection rule. The obvious first improvement -- dropping the sets that
    // turned out redundant once everything was covered -- is left to you.
    vector<int32_t> chosen = greedy();

    write_cover(argv[2], chosen);
    int64_t total = 0;
    for (int32_t j : chosen) total += COST[j];
    std::fprintf(stderr, "cover cost %lld (%zu sets)\n", (long long)total, chosen.size());
    return 0;
}
