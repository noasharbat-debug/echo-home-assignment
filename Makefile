# Root-level Makefile for Echo.
#
# `make test` is the Mission 4 compatibility test suite entry point (see
# test/test_compat.py and PROGRESS.md's "Mission 4" section for full design
# notes). It requires:
#   - a working Docker daemon
#   - the `echo-nginx` image already built (`docker build -f Containerfile -t
#     echo-nginx .` from the repo root - see PROGRESS.md Mission 3)
#   - the `nginx:1.25-bookworm` baseline image (pulled automatically by
#     `docker run` if not already present locally)
#   - Python 3, stdlib only, no pip installs required
#
# PYTHON defaults to `py` (the Windows Python Launcher), which is what this
# host actually has working end-to-end (the `python`/`python3` names on PATH
# here are broken Microsoft Store shim aliases - see PROGRESS.md Mission 4).
# Override on the command line if your environment differs, e.g.:
#   make test PYTHON=python3
#
# `make all` is the single command a reviewer should need on a clean clone:
# it builds the .deb from source (build/Makefile's `build` target, which
# produces the echo-nginx-builder image + build/output/*.deb), builds the
# final runtime image from it (`docker build -f Containerfile -t echo-nginx .`),
# then runs the compatibility test suite (`make test`, above). Each step
# depends on the previous one's output, so `make all` alone is sufficient
# end to end - no manual ordering required.
#
# NOTE: $(MAKE) is quoted below deliberately. On a host where GNU Make isn't
# on PATH (e.g. a fresh GnuWin32 install at "C:/Program Files (x86)/GnuWin32/
# bin/make.exe"), $(MAKE) expands to that path verbatim, including the spaces
# and parentheses - unquoted, the recipe's shell chokes on it ("syntax error
# near unexpected token `('"). This was caught for real during Mission 5's
# clean-clone reproducibility run and is not a hypothetical.

PYTHON ?= py

.PHONY: all test build image

all: build image test

build:
	"$(MAKE)" -C build build

image:
	docker build -f Containerfile -t echo-nginx .

test:
	$(PYTHON) test/test_compat.py
