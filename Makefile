# cheerleader — build targets
#
# `make standalone` produces a self-contained single-file CLI (a shiv zipapp
# bundling the application and every dependency, including the optional
# capstone backend) in dist/. The artifact needs only python3 >= 3.11 on the
# host (no uv/pip/venv); it unpacks itself into ~/.shiv on first run.

SHELL      := /bin/bash
VERSION    := $(shell sed -n 's/^version = "\(.*\)"/\1/p' pyproject.toml)
PLATFORM   := $(shell uname -s | tr '[:upper:]' '[:lower:]')-$(shell uname -m)
DIST       := dist
BUILD_DIR  := .build
STAGE_VENV := $(BUILD_DIR)/venv
SP_DIR     := $(STAGE_VENV)/lib/python3.11/site-packages
ARTIFACT   := cheerleader-$(VERSION)-$(PLATFORM)

.PHONY: standalone standalone-test standalone-tui-test clean

## Build the standalone single-file CLI into $(DIST)/
standalone:
	rm -rf $(BUILD_DIR) $(DIST)
	mkdir -p $(DIST)
	uv venv --python 3.11 $(STAGE_VENV)
	uv pip install --python $(STAGE_VENV)/bin/python ".[capstone]" shiv
	$(STAGE_VENV)/bin/shiv -c cheerleader --site-packages $(SP_DIR) -o $(DIST)/cheerleader
	install -d $(DIST)/skills
	cp -r skills/. $(DIST)/skills/
	cp env.template $(DIST)/
	tar -czf $(DIST)/$(ARTIFACT).tar.gz -C $(DIST) cheerleader skills env.template
	@echo
	@echo "Standalone CLI : $(DIST)/cheerleader"
	@echo "Distro archive : $(DIST)/$(ARTIFACT).tar.gz"
	@echo "Host requirement: python3 >= 3.11 (no uv/pip/venv needed)"

## Smoke-test the standalone artifact (CLI entry + import chain + error paths)
standalone-test:
	test -x $(DIST)/cheerleader
	$(DIST)/cheerleader --help >/dev/null
	! $(DIST)/cheerleader /nonexistent
	@echo "Standalone smoke tests passed"

## Launch the TUI in a pty and verify the app comes up (needs a real TTY)
standalone-tui-test:
	@if [ "$$(uname)" = "Darwin" ]; then \
		launch='timeout 15 script -q /dev/null sh -c "$(DIST)/cheerleader /bin/ls --disasm-engine=capstone"'; \
	else \
		launch='timeout 15 script -qec "$(DIST)/cheerleader /bin/ls --disasm-engine=capstone" /dev/null'; \
	fi; \
	if ! $$launch >/dev/null 2>&1; then \
		rc=$$?; [ $$rc -eq 124 ] || exit $$rc; \
	fi
	@echo "TUI launch test passed"

clean:
	rm -rf $(BUILD_DIR) $(DIST) build wheels
