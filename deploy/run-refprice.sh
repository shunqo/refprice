#!/usr/bin/env bash
# One collection pass, cron-safe. Starts no server, opens no port.
set -euo pipefail
cd "$(dirname "$0")/.."
exec .venv/bin/python -m refprice.cli --quiet run
