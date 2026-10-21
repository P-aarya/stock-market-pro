#!/bin/sh
# Runs every test: the dashboard's JavaScript (needs Node) and the Python modules.
# None of them need a database, the internet or an API key.
#   sh tests/run_tests.sh
cd "$(dirname "$0")/.." || exit 1
status=0

if command -v node >/dev/null 2>&1; then
  for f in tests/js/*.test.js; do node "$f" || status=1; done
else
  echo "Node.js not found: skipping the JavaScript tests"
fi

PY="python3"; [ -x venv/bin/python ] && PY="venv/bin/python"
(cd tests/py && "../../$PY" -m unittest discover -s . -p "test_*.py" -v 2>&1 | grep -v "Matplotlib\|plotly") || status=1

[ "$status" -eq 0 ] && echo "ALL TESTS PASSED" || echo "SOME TESTS FAILED"
exit $status
