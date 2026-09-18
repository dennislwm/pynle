function check_uv {
  command -v uv > /dev/null 2>&1 || { echo "[ERROR][$FUNCNAME]: uv not installed."; return 1; }
  echo "[OK]   uv found ($(uv --version 2>&1 | head -1))"
}

function check_cmake {
  command -v cmake > /dev/null 2>&1 || { echo "[WARN][$FUNCNAME]: cmake not installed. 'make build' will fail. See README.md Installation (brew install cmake on macOS)."; return 0; }
  echo "[OK]   cmake found ($(cmake --version 2>&1 | head -1))"
}

function check_pre_commit {
  command -v pre-commit > /dev/null 2>&1 || { echo "[WARN][$FUNCNAME]: pre-commit not installed. It ships via 'make build' dev extras; run that first."; return 0; }
  if [ -f .git/hooks/pre-commit ]; then
    echo "[OK]   pre-commit found and git hook installed"
  else
    echo "[NONE][$FUNCNAME]: pre-commit found but git hook not installed. Run 'make setup'."
  fi
}

function setup_pre_commit {
  command -v pre-commit > /dev/null 2>&1 || { echo "[WARN][$FUNCNAME]: pre-commit not installed (installed via 'make build' dev extras). Run 'make build' first."; return 0; }
  if [ -f .git/hooks/pre-commit ]; then
    echo "[SKIP] pre-commit git hook already installed"
  else
    pre-commit install
    echo "[OK]   pre-commit git hook installed"
  fi
}

# Fails if any pyproject.toml dependency lacks a version pin/bound.
# A dependency that self-references this project's own extras (e.g.
# "nle[dev]" in [dependency-groups]) is not flagged -- it isn't a
# third-party package that can drift to a breaking version.
function check_pins {
  command -v python3 > /dev/null 2>&1 || { echo "[ERROR][$FUNCNAME]: python3 not found."; return 1; }
  local unpinned
  unpinned=$(python3 - <<'EOF'
import re, sys, tomllib
with open("pyproject.toml", "rb") as f:
    data = tomllib.load(f)
project = data.get("project", {})
name = project.get("name", "")
deps = list(project.get("dependencies", []))
for group in project.get("optional-dependencies", {}).values():
    deps.extend(group)
for group in data.get("dependency-groups", {}).values():
    deps.extend(group)
pin_re = re.compile(r"(==|>=|<=|~=|!=|>|<)")
self_re = re.compile(rf"^{re.escape(name)}(\[|$)") if name else None
unpinned = [d for d in deps if not pin_re.search(d) and not (self_re and self_re.match(d))]
print("\n".join(unpinned))
sys.exit(1 if unpinned else 0)
EOF
)
  if [ -n "$unpinned" ]; then
    echo "[ERROR][$FUNCNAME]: unpinned dependencies found in pyproject.toml:"
    echo "$unpinned" | sed 's/^/       /'
    return 1
  fi
  echo "[OK]   all dependencies in pyproject.toml are pinned"
}

function show_status {
  echo "=== Status ==="
  check_uv
  check_cmake || true
  check_pre_commit || true
  check_pins || true
  echo "=============="
}

function setup_commands {
  echo "=== pynle Setup ==="
  check_uv
  check_cmake || true
  setup_pre_commit
  echo "==================="
}
