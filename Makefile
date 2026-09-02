PYTHON ?= python3
SOFT_RASTER_DIR ?= ../../kilix-modules/soft-raster

SOURCES := $(shell find src -name '*.py')
TESTS := $(wildcard tests/*.py)

.DEFAULT_GOAL := help

.PHONY: all help check compile test test-raster examples clean wheel install

# `all` is what the Kilix content installer runs, and what every other
# git-sourced catalog entry declares. It builds; it does not test. A user's
# install must not depend on a test suite passing on their machine.
all: compile
	@test -x ./kilix-graphs || { echo 'kilix-graphs launcher is not executable'; exit 1; }

help:
	@printf '%s\n' \
		'kilix-graphs' \
		'' \
		'  make all          build (what the content installer runs)' \
		'  make check        compile + the full suite' \
		'  make test         the suite on a bare interpreter' \
		'  make test-raster  the suite with soft-raster on the path' \
		'  make examples     render every example to build/' \
		'  make wheel        build a wheel into dist/' \
		'' \
		'  SOFT_RASTER_DIR=$(SOFT_RASTER_DIR)'

compile:
	$(PYTHON) -m compileall -q src tests kilix-graphs

test:
	PYTHONPATH=src $(PYTHON) -m unittest discover -s tests

# The raster backend needs both the binding and the built native library.
# Without them its tests skip rather than fail, which is why `test` above is
# still a meaningful run.
test-raster:
	@test -f "$(SOFT_RASTER_DIR)/build/libsoft-raster.so" || \
		{ echo "build soft-raster first: make -C $(SOFT_RASTER_DIR)"; exit 1; }
	PYTHONPATH=src:$(SOFT_RASTER_DIR)/python/src \
	SOFT_RASTER_LIBRARY=$(abspath $(SOFT_RASTER_DIR)/build/libsoft-raster.so) \
		$(PYTHON) -m unittest discover -s tests

check: compile test

examples: | build
	@set -eu; for source in examples/*.kg examples/*.dot; do \
		[ -e "$$source" ] || continue; \
		name=$$(basename "$$source"); name=$${name%.*}; \
		PYTHONPATH=src $(PYTHON) -m kilix_graphs.cli draw "$$source" \
			-o "build/$$name.svg"; \
		PYTHONPATH=src $(PYTHON) -m kilix_graphs.cli draw "$$source" \
			-r text --no-colour -o "build/$$name.txt"; \
		printf 'build/%s.svg build/%s.txt\n' "$$name" "$$name"; \
	done
	@set -eu; for source in examples/*.csv; do \
		[ -e "$$source" ] || continue; \
		name=$$(basename "$$source" .csv); \
		PYTHONPATH=src $(PYTHON) -m kilix_graphs.cli chart "$$source" \
			-o "build/$$name.svg"; \
		printf 'build/%s.svg\n' "$$name"; \
	done

build:
	mkdir -p build

wheel:
	$(PYTHON) -m pip wheel --no-deps --no-build-isolation --wheel-dir dist .

install:
	$(PYTHON) -m pip install .

clean:
	rm -rf build dist src/*.egg-info src/kilix_graphs/__pycache__ \
		src/kilix_graphs/*/__pycache__ tests/__pycache__
