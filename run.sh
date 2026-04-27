#!/usr/bin/env bash
# ============================================================================
# run.sh  —  XAUUSD Quant Session-Sweep Backtest Runner
# ============================================================================
#
# Usage:
#   ./run.sh                          # full backtest with default config
#   ./run.sh --dry-run                # validate data + config only
#   ./run.sh --config configs/config.yaml --log-level DEBUG
#   ./run.sh --csv xauusd_1m.csv --trade-start 2026-03-01 --trade-end 2026-03-31
#
# Exit codes are forwarded from backtest.py:
#   0 = success, 1 = data error, 2 = config error, 3 = backtest failure
# ============================================================================

set -euo pipefail

# ── Colour helpers ────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# ── Discover project root ─────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# ── Pre-flight checks ─────────────────────────────────────────────────
echo ""
echo -e "${GREEN}╔════════════════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║  XAUUSD BACKTEST RUNNER                                ║${NC}"
echo -e "${GREEN}╚════════════════════════════════════════════════════════╝${NC}"
echo ""

# Check Python
if ! command -v python &>/dev/null; then
    echo -e "${RED}[ERROR] Python not found in PATH.${NC}"
    exit 1
fi
PYTHON_VERSION=$(python --version 2>&1)
echo -e "  Python:   ${PYTHON_VERSION}"

# Check required packages
MISSING=""
for pkg in backtrader pandas numpy pyyaml pydantic; do
    if ! python -c "import $pkg" 2>/dev/null; then
        MISSING="$MISSING $pkg"
    fi
done
if [ -n "$MISSING" ]; then
    echo -e "${RED}[ERROR] Missing packages:${MISSING}${NC}"
    echo "  Install with: pip install -r requirements.txt"
    exit 1
fi
echo -e "  Packages: all satisfied"

# Check that config file exists (unless --help)
CONFIG_ARG=""
for arg in "$@"; do
    if [ "$arg" = "--help" ] || [ "$arg" = "-h" ]; then
        python backtest.py --help
        exit 0
    fi
done

# Default config if not specified
CONFIG_FILE="configs/config_30d.yaml"
for i in $(seq 1 $#); do
    if [ "${!i}" = "--config" ]; then
        next=$((i + 1))
        CONFIG_FILE="${!next}"
    fi
done

if [ ! -f "$CONFIG_FILE" ]; then
    echo -e "${YELLOW}[WARN] Config file '$CONFIG_FILE' not found.${NC}"
    echo "  Default config will be used."
fi

# Check data file
CSV_FILE="xauusd_1m.csv"
for i in $(seq 1 $#); do
    if [ "${!i}" = "--csv" ]; then
        next=$((i + 1))
        CSV_FILE="${!next}"
    fi
done

if [ ! -f "$CSV_FILE" ] && [[ "$*" != *"--dry-run"* ]]; then
    echo -e "${YELLOW}[WARN] Data file '$CSV_FILE' not found.${NC}"
    echo "  Run with --dry-run to validate config only."
fi

# ── Run ───────────────────────────────────────────────────────────────
echo ""
echo "  ─────────────────────────────────────────"
echo "  Running: python backtest.py $*"
echo "  ─────────────────────────────────────────"
echo ""

# Capture exit code
EXIT_CODE=0
python backtest.py "$@" || EXIT_CODE=$?

# ── Report ────────────────────────────────────────────────────────────
echo ""
echo "  ─────────────────────────────────────────"
case $EXIT_CODE in
    0)
        echo -e "  ${GREEN}[PASS] Backtest completed successfully.${NC}"
        ;;
    1)
        echo -e "  ${RED}[FAIL] Data error (exit code 1).${NC}"
        echo "  Check that XAUUSD 1M CSV exists and has valid columns: datetime,open,high,low,close,volume"
        ;;
    2)
        echo -e "  ${RED}[FAIL] Config error (exit code 2).${NC}"
        echo "  Check that your YAML config file exists and matches the schema."
        ;;
    3)
        echo -e "  ${RED}[FAIL] Backtest runtime error (exit code 3).${NC}"
        echo "  Check logs/backtest.log for details."
        ;;
    *)
        echo -e "  ${RED}[FAIL] Unexpected exit code: $EXIT_CODE${NC}"
        ;;
esac
echo "  ─────────────────────────────────────────"

exit $EXIT_CODE