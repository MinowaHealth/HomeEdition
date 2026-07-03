#!/bin/bash
set -e
# setup.sh — One-shot developer environment setup.
#
# Creates the repo-root .venv, installs the UserApp runtime deps plus the dev
# tooling (pyright/pre-commit/pip-audit), and wires up the git hooks — the
# two-file install from TESTING.md, in one command. Run from the repo root:
#
#   ./setup.sh
#
# Working on the MCP server too? Add its runtime deps afterward:
#   .venv/bin/pip install -r UserMCP/requirements.txt

python3.12 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r UserApp/webapp/requirements.txt -r requirements-dev.txt -r DataModel3/requirements.txt
.venv/bin/pre-commit install

echo "Dev environment ready. See TESTING.md for the test loop."
