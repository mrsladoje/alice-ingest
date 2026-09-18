#!/bin/sh
set -e
cd "$(dirname "$0")"
tectonic --keep-logs report.tex
