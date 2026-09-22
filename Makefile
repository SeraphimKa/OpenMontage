PYTHON_VERSION ?= 3.10
VENV_DIR ?= .venv
BASE_PYTHON ?= $(shell command -v python$(PYTHON_VERSION) 2>/dev/null || command -v python3 2>/dev/null || command -v python 2>/dev/null)
RUN_PYTHON = $(shell for dir in "$$VIRTUAL_ENV" "$$CONDA_PREFIX" "$(VENV_DIR)"; do if [ -n "$$dir" ] && [ -x "$$dir/bin/python" ]; then printf "%s/bin/python" "$$dir"; exit 0; elif [ -n "$$dir" ] && [ -x "$$dir/Scripts/python.exe" ]; then printf "%s/Scripts/python.exe" "$$dir"; exit 0; fi; done; if [ "$(OS)" = "Windows_NT" ]; then printf "%s/Scripts/python.exe" "$(VENV_DIR)"; else printf "%s/bin/python" "$(VENV_DIR)"; fi)
PIP = $(RUN_PYTHON) -m pip
# uv ignores BASE_PYTHON unless told; honour it when given on the command line.
UV_PYTHON = $(if $(filter command line,$(origin BASE_PYTHON)),$(BASE_PYTHON),$(PYTHON_VERSION))
UV_PYTHON_LABEL = $(if $(filter command line,$(origin BASE_PYTHON)),$(BASE_PYTHON),Python $(PYTHON_VERSION)+)

.DEFAULT_GOAL := setup

.PHONY: setup install install-dev install-gpu test test-contracts lint clean preflight piper-voice demo demo-list hyperframes-doctor hyperframes-warm venv ensure-venv shot shot-go shot-sheet prompt-lint assemble cut-sheet stop stop-ok

# ---- Virtual environment ----

ensure-venv:
	@if [ -n "$$VIRTUAL_ENV" ] && { [ -x "$$VIRTUAL_ENV/bin/python" ] || [ -x "$$VIRTUAL_ENV/Scripts/python.exe" ]; }; then \
		echo "==> Using active virtual environment: $$VIRTUAL_ENV"; \
	elif [ -n "$$CONDA_PREFIX" ] && { [ -x "$$CONDA_PREFIX/bin/python" ] || [ -x "$$CONDA_PREFIX/Scripts/python.exe" ]; }; then \
		echo "==> Using active conda environment: $$CONDA_PREFIX"; \
	elif [ -x "$(VENV_DIR)/bin/python" ] || [ -x "$(VENV_DIR)/Scripts/python.exe" ]; then \
		echo "==> Using existing virtual environment: $(VENV_DIR)"; \
	elif command -v uv >/dev/null 2>&1; then \
		echo "==> Creating virtual environment with uv ($(UV_PYTHON_LABEL)): $(VENV_DIR)"; \
		uv venv --python "$(UV_PYTHON)" "$(VENV_DIR)"; \
	else \
		if [ -z "$(BASE_PYTHON)" ]; then \
			echo "ERROR: Python $(PYTHON_VERSION)+ is required, but no python executable was found."; \
			exit 1; \
		fi; \
		"$(BASE_PYTHON)" -c "import sys; required=tuple(map(int, '$(PYTHON_VERSION)'.split('.')[:2])); raise SystemExit(0 if sys.version_info[:2] >= required else 1)" || { \
			echo "ERROR: OpenMontage requires Python $(PYTHON_VERSION)+."; \
			echo "Install uv or Python $(PYTHON_VERSION)+, then run make again."; \
			exit 1; \
		}; \
		echo "==> Creating virtual environment with Python venv: $(VENV_DIR)"; \
		"$(BASE_PYTHON)" -m venv "$(VENV_DIR)" || { \
			echo "ERROR: Could not create $(VENV_DIR). Install uv or ensure python venv support is available."; \
			exit 1; \
		}; \
	fi
	@$(RUN_PYTHON) -c "import sys; required=tuple(map(int, '$(PYTHON_VERSION)'.split('.')[:2])); raise SystemExit(0 if sys.version_info[:2] >= required else 1)" || { \
		echo "ERROR: OpenMontage requires Python $(PYTHON_VERSION)+."; \
		echo "Current interpreter is $$($(RUN_PYTHON) -c 'import sys; print(\".\".join(map(str, sys.version_info[:3])))' 2>/dev/null || echo unavailable): $(RUN_PYTHON)"; \
		echo "Activate a compatible environment or remove it so make can create $(VENV_DIR)."; \
		exit 1; \
	}
	@$(RUN_PYTHON) -m pip --version >/dev/null 2>&1 || $(RUN_PYTHON) -m ensurepip --upgrade >/dev/null

venv: ensure-venv
	@echo "==> Virtual environment ready."
	@echo "    Python: $(RUN_PYTHON)"
	@if [ -z "$$VIRTUAL_ENV" ] && [ -z "$$CONDA_PREFIX" ]; then if [ "$(OS)" = "Windows_NT" ]; then echo "    Activate with: $(VENV_DIR)\\Scripts\\Activate.ps1"; else echo "    Activate with: source $(VENV_DIR)/bin/activate"; fi; fi

# ---- One-command setup ----

setup: ensure-venv
	@echo "==> Installing Python dependencies..."
	$(PIP) install -r requirements.txt
	@echo ""
	@echo "==> Installing Remotion composer..."
	cd remotion-composer && npm install
	@echo ""
	@$(MAKE) --no-print-directory piper-voice
	@echo ""
	@echo "==> Installing HyperFrames runtime (cache-warm via npx)..."
	@echo "    Pulls the 'hyperframes' npm package into the local npx cache so the"
	@echo "    first render doesn't pay a 30-60s cold-fetch penalty. ~20MB of disk."
	@npx --yes hyperframes --version >/dev/null 2>&1 && echo "    HyperFrames CLI cached (npx)" || echo "  [skip] HyperFrames cache-warm failed — offline or npm unavailable; first render will fetch on demand"
	@$(RUN_PYTHON) -c "from tools.video.hyperframes_compose import HyperFramesCompose; HyperFramesCompose._npm_resolve_cache=None; c=HyperFramesCompose()._runtime_check(); print(f'    HyperFrames runtime_available={c[\"runtime_available\"]}, npm={c.get(\"npm_package_version\") or c.get(\"npm_resolve_error\")}'); [print(f'    note: {r}') for r in c['reasons']]" || echo "  [skip] HyperFrames check failed — runtime can be set up later"
	@echo ""
	$(RUN_PYTHON) -c "import shutil, os; e=os.path.exists('.env'); shutil.copy('.env.example','.env') if not e else None; print('==> Created .env from .env.example — add your API keys there.' if not e else '==> .env already exists — skipping.')"
	@echo ""
	@echo "Done! Open this project in your AI coding assistant and start creating."
	@echo "  Optional: add API keys to .env to unlock cloud providers."
	@echo "  Optional: run 'make install-gpu' if you have an NVIDIA GPU."
	@echo "  Optional: run 'make hyperframes-doctor' to fully validate the HyperFrames runtime."
	@echo "  Optional: run 'make hyperframes-warm' anytime to refresh the npx cache to the latest hyperframes version."

# ---- Individual installs ----

install: ensure-venv
	$(PIP) install -r requirements.txt

install-dev: ensure-venv
	$(PIP) install -r requirements-dev.txt

install-gpu: ensure-venv
	$(PIP) install -r requirements-gpu.txt
	$(PIP) install diffusers transformers accelerate

# ---- Testing ----

test: ensure-venv
	$(RUN_PYTHON) -m pytest tests/ -v

test-contracts: ensure-venv
	$(RUN_PYTHON) -m pytest tests/contracts/ -v

# ---- open-montage shots ----
# One contracted shot at a time, from projects/$(PROJECT)/artifacts/shot_contract.json.
# `make shot` only plans and prices; nothing is spent until `make shot-go`.

shot: ensure-venv
	@test -n "$(PROJECT)" -a -n "$(SHOT)" || { echo "usage: make shot PROJECT=<id> SHOT=<shot_id>"; exit 1; }
	$(RUN_PYTHON) scripts/om_shot.py $(PROJECT) $(SHOT)

shot-go: ensure-venv
	@test -n "$(PROJECT)" -a -n "$(SHOT)" || { echo "usage: make shot-go PROJECT=<id> SHOT=<shot_id>"; exit 1; }
	$(RUN_PYTHON) scripts/om_shot.py $(PROJECT) $(SHOT) --go

shot-sheet: ensure-venv
	@test -n "$(PROJECT)" -a -n "$(SHOT)" || { echo "usage: make shot-sheet PROJECT=<id> SHOT=<shot_id>"; exit 1; }
	$(RUN_PYTHON) scripts/om_shot.py $(PROJECT) $(SHOT) --sheet

prompt-lint: ensure-venv
	@test -n "$(PROJECT)" || { echo "usage: make prompt-lint PROJECT=<id>"; exit 1; }
	$(RUN_PYTHON) scripts/om_prompt_lint.py lint $(PROJECT)

assemble: ensure-venv
	@test -n "$(PROJECT)" || { echo "usage: make assemble PROJECT=<id> [GRADE=amber-blue-night|none]"; exit 1; }
	$(RUN_PYTHON) scripts/om_assemble.py $(PROJECT) $(if $(GRADE),--grade $(GRADE),)

cut-sheet: ensure-venv
	@test -n "$(PROJECT)" || { echo "usage: make cut-sheet PROJECT=<id>"; exit 1; }
	$(RUN_PYTHON) scripts/om_assemble.py $(PROJECT) --sheet

# The skill's four stops on the Backlot stage rail: brief, references, shots, cut.
# `stop` writes awaiting_human and you end your turn; `stop-ok` records the approval.

stop: ensure-venv
	@test -n "$(PROJECT)" -a -n "$(STOP)" || { echo "usage: make stop PROJECT=<id> STOP=brief|references|shots|cut"; exit 1; }
	$(RUN_PYTHON) scripts/om_stop.py $(PROJECT) $(STOP) $(if $(NOTE),--note "$(NOTE)",)

stop-ok: ensure-venv
	@test -n "$(PROJECT)" -a -n "$(STOP)" || { echo "usage: make stop-ok PROJECT=<id> STOP=brief|references|shots|cut"; exit 1; }
	$(RUN_PYTHON) scripts/om_stop.py $(PROJECT) $(STOP) --approve $(if $(NOTE),--note "$(NOTE)",)

# ---- Utilities ----

piper-voice: ensure-venv
	@echo "==> Downloading default Piper voice (en_US-lessac-medium)..."
	@if [ -f en_US-lessac-medium.onnx ]; then \
		echo "    en_US-lessac-medium.onnx already present — skipping."; \
	else \
		$(RUN_PYTHON) -m piper.download_voices en_US-lessac-medium || echo "  [skip] Piper voice download failed — run 'make piper-voice' later, or TTS will use cloud providers instead"; \
	fi

preflight: ensure-venv
	PATH="$$(cd "$$(dirname "$(RUN_PYTHON)")" && pwd):$$PATH" $(RUN_PYTHON) -c "from tools.tool_registry import registry; import json; registry.discover(); print(json.dumps(registry.provider_menu(), indent=2))"

hyperframes-doctor: ensure-venv
	@echo "==> Probing HyperFrames runtime (node/ffmpeg/npx + hyperframes doctor)..."
	$(RUN_PYTHON) -c "from tools.video.hyperframes_compose import HyperFramesCompose; r=HyperFramesCompose().execute({'operation':'doctor'}); import json; print(json.dumps(r.data, indent=2)); print('OK' if r.success else f'FAIL: {r.error}')"

hyperframes-warm:
	@echo "==> Refreshing the HyperFrames npx cache to latest..."
	@echo "    Uses --prefer-online so npx picks up new releases since your last run."
	npx --yes --prefer-online hyperframes --version
	@echo "==> Cache warm complete."

demo: ensure-venv
	@echo "==> Rendering zero-key demo videos (no API keys needed)..."
	@echo "    These use only Remotion components — animated charts, text, data viz."
	@echo ""
	$(RUN_PYTHON) render_demo.py

demo-list: ensure-venv
	$(RUN_PYTHON) render_demo.py --list

lint: ensure-venv
	$(RUN_PYTHON) -m py_compile tools/base_tool.py
	$(RUN_PYTHON) -m py_compile tools/tool_registry.py
	$(RUN_PYTHON) -m py_compile tools/cost_tracker.py
	$(RUN_PYTHON) -m py_compile tools/analysis/composition_validator.py

clean:
	$(BASE_PYTHON) -c "import pathlib, shutil; excluded=[pathlib.Path('$(VENV_DIR)'), pathlib.Path('venv')]; skip=lambda p: any(p == root or root in p.parents for root in excluded); roots=[p for p in pathlib.Path('.').rglob('__pycache__') if not skip(p)]; [shutil.rmtree(p) for p in roots]; files=[p for p in pathlib.Path('.').rglob('*.pyc') if not skip(p)]; [p.unlink() for p in files]"
