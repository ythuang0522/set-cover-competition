CXX      ?= c++
CXXFLAGS ?= -O2 -std=c++17 -Wall -Wextra

# macOS workaround: some Command Line Tools installs are missing headers in
# /Library/Developer/CommandLineTools/usr/include/c++/v1. Fall back to the
# SDK's c++/v1 when the default location is broken. No-op on Linux.
UNAME_S := $(shell uname -s)
ifeq ($(UNAME_S),Darwin)
    ifeq ($(wildcard /Library/Developer/CommandLineTools/usr/include/c++/v1/cstdint),)
        SDK_CXX_INC := $(shell xcrun --sdk macosx --show-sdk-path 2>/dev/null)/usr/include/c++/v1
        ifneq ($(wildcard $(SDK_CXX_INC)/cstdint),)
            CXXFLAGS += -isystem $(SDK_CXX_INC)
        endif
    endif
endif

.PHONY: all foundation solver tools test check-data clean

all: foundation

# The starting point: greedy set cover (slide 13).
foundation: setcover_foundation.cpp
	$(CXX) $(CXXFLAGS) -o foundation setcover_foundation.cpp

# Your solver. Start with:  cp setcover_foundation.cpp solver.cpp
solver: solver.cpp
	$(CXX) $(CXXFLAGS) -o solver solver.cpp

# --- instructor / tooling targets ------------------------------------------

# The Lagrangian lower-bound tool. Needed only to make your own practice
# instances with a bound.
tools: tools/bound/bound
tools/bound/bound: tools/bound/bound.cpp
	$(CXX) $(CXXFLAGS) -o tools/bound/bound tools/bound/bound.cpp


# The reference solver (instructor only; the directory is gitignored).
tools/ref/solvers/refsolve: tools/ref/solvers/refsolve.cpp
	$(CXX) $(CXXFLAGS) -o $@ $<

# Regression tests for grade.py (cover verification, the failure accounting,
# the enforced time/memory/thread limits).
test: foundation
	python3 tools/test_grade.py

# Verify every instance against its meta file (digest, m/n, the stored bound
# is not above the reference cover, the bound tool reproduces it) -- slow.
check-data: tools
	python3 tools/pipeline.py check

clean:
	rm -f foundation solver tools/bound/bound tools/ref/solvers/refsolve
