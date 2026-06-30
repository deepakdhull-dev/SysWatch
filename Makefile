.PHONY: proto proto-check lint build build-agent build-server build-frontend clean help

# ── Paths ─────────────────────────────────────────────────────────────────────
PROTO_SRC      := proto/syswatch.proto
AGENT_PROTO    := syswatch-agent/syswatch_agent/proto
SERVER_PROTO   := syswatch-server/syswatch_server/proto
AGENT_PKG      := syswatch-agent
SERVER_PKG     := syswatch-server
FRONTEND_DIR   := syswatch-server/frontend
DIST_DIR       := dist

# ── Proto ─────────────────────────────────────────────────────────────────────
proto:
	@echo "[proto] generating from $(PROTO_SRC)"
	uv run python3 -m grpc_tools.protoc \
		--proto_path=proto \
		--python_out=$(AGENT_PROTO) \
		--grpc_python_out=$(AGENT_PROTO) \
		syswatch.proto
	uv run python3 -m grpc_tools.protoc \
		--proto_path=proto \
		--python_out=$(SERVER_PROTO) \
		--grpc_python_out=$(SERVER_PROTO) \
		syswatch.proto
	@# Fix absolute imports emitted by protoc → relative imports within subpackage
	sed -i 's/^import syswatch_pb2/from . import syswatch_pb2/' \
		$(AGENT_PROTO)/syswatch_pb2_grpc.py \
		$(SERVER_PROTO)/syswatch_pb2_grpc.py
	@echo "[proto] done"

# CI guard: generated files must match what protoc would produce right now.
# Run `make proto` if this fails.
proto-check:
	@echo "[proto-check] verifying generated files are up to date"
	@$(MAKE) proto AGENT_PROTO=/tmp/proto_check_agent SERVER_PROTO=/tmp/proto_check_server \
		--no-print-directory 2>/dev/null; \
	for f in syswatch_pb2.py syswatch_pb2_grpc.py; do \
		diff -q $(AGENT_PROTO)/$$f /tmp/proto_check_agent/$$f \
			|| (echo "DRIFT: $(AGENT_PROTO)/$$f out of date — run make proto"; exit 1); \
		diff -q $(SERVER_PROTO)/$$f /tmp/proto_check_server/$$f \
			|| (echo "DRIFT: $(SERVER_PROTO)/$$f out of date — run make proto"; exit 1); \
	done
	@echo "[proto-check] ok"

# ── Lint ──────────────────────────────────────────────────────────────────────
lint:
	@echo "[lint] ruff + mypy"
	uv run ruff check syswatch-agent/syswatch_agent syswatch-server/syswatch_server
	uv run mypy syswatch-agent/syswatch_agent syswatch-server/syswatch_server \
		--ignore-missing-imports

# ── Build ─────────────────────────────────────────────────────────────────────
$(DIST_DIR):
	mkdir -p $(DIST_DIR)

build-agent: proto $(DIST_DIR)
	@echo "[build] agent wheel"
	uv build $(AGENT_PKG) --out-dir $(DIST_DIR)

build-frontend:
	@echo "[build] frontend (React + Vite)"
	cd $(FRONTEND_DIR) && npm install && npm run build
	test -f syswatch-server/syswatch_server/static/index.html || (echo "ERROR: vite build did not produce static/index.html"; exit 1)
	@echo "[build] frontend ok"

build-server: proto build-frontend $(DIST_DIR)
	@echo "[build] server wheel"
	uv build $(SERVER_PKG) --out-dir $(DIST_DIR)

build: build-agent build-server
	@echo "[build] artifacts in $(DIST_DIR)/"
	ls -lh $(DIST_DIR)/

# ── Clean ─────────────────────────────────────────────────────────────────────
clean:
	rm -rf $(DIST_DIR)
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name "*.egg-info" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".mypy_cache" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".ruff_cache" -exec rm -rf {} + 2>/dev/null || true
	@echo "[clean] done"

# ── Help ──────────────────────────────────────────────────────────────────────
help:
	@echo "Targets:"
	@echo "  proto          Generate pb2 files into both packages"
	@echo "  proto-check    CI drift check (fails if generated files are stale)"
	@echo "  lint           ruff + mypy across both packages"
	@echo "  build          Agent wheel + server wheel (includes frontend check)"
	@echo "  build-agent    Agent wheel only"
	@echo "  build-server   Server wheel only"
	@echo "  build-frontend Validate frontend asset dirs"
	@echo "  clean          Remove all build artifacts and caches"
