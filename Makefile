.PHONY: help setup setup-daemon setup-ui dev-daemon dev-ui clean build build-daemon build-ui lint test

DAEMON_DIR := daemon
UI_DIR := ui
VENV := $(DAEMON_DIR)/.venv
PYTHON := $(VENV)/bin/python
PIP := $(VENV)/bin/pip

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

setup: setup-daemon setup-ui ## Set up both daemon and UI development environments

setup-daemon: ## Set up Python daemon venv and install dependencies
	cd $(DAEMON_DIR) && python3 -m venv .venv
	$(PIP) install -e ".[dev]"

setup-ui: ## Set up Tauri UI project dependencies
	cd $(UI_DIR) && npm install

dev-daemon: ## Run daemon in development mode
	$(PYTHON) -m gdrive_sync --log-level debug

dev-ui: ## Run Tauri UI in development mode
	cd $(UI_DIR) && npm run tauri dev

clean: ## Clean build artifacts
	rm -rf $(DAEMON_DIR)/.venv $(DAEMON_DIR)/dist $(DAEMON_DIR)/build
	rm -rf $(UI_DIR)/node_modules $(UI_DIR)/src-tauri/target
	rm -rf artifacts/

build: build-daemon build-ui ## Build both daemon and UI

build-daemon: ## Build daemon with Docker (PyInstaller)
	docker build -f docker/Dockerfile.daemon -t gdrive-sync-daemon-builder .
	mkdir -p artifacts
	docker run --rm -v $(PWD)/artifacts:/out gdrive-sync-daemon-builder

build-ui: ## Build Tauri UI with Docker
	docker build -f docker/Dockerfile.ui -t gdrive-sync-ui-builder .
	mkdir -p artifacts
	docker run --rm -v $(PWD)/artifacts:/out gdrive-sync-ui-builder

lint: ## Run linters
	cd $(DAEMON_DIR) && $(VENV)/bin/ruff check src/ tests/
	cd $(UI_DIR) && npx tsc --noEmit

test: ## Run all tests
	cd $(DAEMON_DIR) && $(VENV)/bin/pytest -v

install-service: ## Install systemd user service
	mkdir -p ~/.config/systemd/user
	cp installer/gdrive-sync-daemon.service ~/.config/systemd/user/
	systemctl --user daemon-reload
	systemctl --user enable gdrive-sync-daemon

uninstall-service: ## Remove systemd user service
	systemctl --user disable gdrive-sync-daemon || true
	systemctl --user stop gdrive-sync-daemon || true
	rm -f ~/.config/systemd/user/gdrive-sync-daemon.service
	systemctl --user daemon-reload
