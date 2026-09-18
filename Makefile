# Hand-authored task-runner Makefile -- NOT generated. Do NOT run
# sys/unix/setup.sh (NetHack's legacy Unix build script) from repo root:
# it overwrites this file with a template-generated one (Makefile.top).
# The actual build path for this fork is 'make build' (uv sync -> CMake
# via scikit-build-core), documented in README.md.
.PHONY: help setup status test build check-pins
SHELL := /bin/bash

help:
	@echo ""
	@echo "=== Targets ==="
	@echo "  help          List available targets"
	@echo "  setup         Check/set up required tools (uv, cmake, pre-commit) -- run first"
	@echo "  status        Check current tool and dependency-pin status"
	@echo "  build         Build the compiled extension via 'uv sync --extra dev'"
	@echo "  test          Run pytest (depends on build)"
	@echo "  check-pins    Fail if any pyproject.toml dependency is unpinned"
	@echo ""

setup:
	@source ./make.sh && setup_commands

status:
	@source ./make.sh && show_status

build:
	uv sync --extra dev

test: build
	uv run pytest

check-pins:
	@source ./make.sh && check_pins
