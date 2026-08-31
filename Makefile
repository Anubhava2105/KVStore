PYTHON ?= python3
ARTIFACT ?= kv.pyz

.PHONY: build run test deps-proof clean

build:
	$(PYTHON) -m zipapp src -o $(ARTIFACT) -p "/usr/bin/env python3" -m "cli:main"

run: build
	$(PYTHON) $(ARTIFACT)

test:
	$(PYTHON) -m unittest discover -s tests -v

deps-proof:
	$(PYTHON) scripts/deps_proof.py > deps-proof.txt

clean:
	$(RM) $(ARTIFACT)
