#!/usr/bin/env bash
# One command.
#   ./run.sh          one collection pass, then serve on http://127.0.0.1:8701/
#   ./run.sh run      one pass, no server
#   ./run.sh show     print the latest reference price and its contributors
#   ./run.sh test     the test suite
set -euo pipefail
cd "$(dirname "$0")"
VENV=.venv; PY="$VENV/bin/python"
if [[ ! -x "$PY" ]]; then
  echo "creating venv..."
  if command -v uv >/dev/null 2>&1; then uv venv "$VENV"; else python3 -m venv "$VENV"; fi
  "$PY" -m pip install -q --upgrade pip
  # `mdq` is declared in pyproject.toml as a git dependency. If you have it checked out next to
  # this repo, that working copy wins, so both halves can be edited in one place.
  if [[ -d ../mdq ]]; then
    "$PY" -m pip install -q -e ../mdq && "$PY" -m pip install -q --no-deps -e .
  else
    "$PY" -m pip install -q -e .
  fi
  "$PY" -m pip install -q pytest ruff
fi
export PYTHONPATH="src${PYTHONPATH:+:$PYTHONPATH}"
case "${1:-serve}" in
  test) exec "$PY" -m pytest -q ;;
  run)  exec "$PY" -m refprice.cli run ;;
  show) exec "$PY" -m refprice.cli show ;;
  serve) "$PY" -m refprice.cli run || true; exec "$PY" -m refprice.cli serve ;;
  *) exec "$PY" -m refprice.cli "$@" ;;
esac
