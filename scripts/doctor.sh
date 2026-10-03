#!/usr/bin/env bash
# Collect everything useful for troubleshooting (service state, log tail, USB/serial/audio devices, config).
#   sudo scripts/doctor.sh > doctor.txt
set -uo pipefail
exec python3 "$(dirname "${BASH_SOURCE[0]}")/rr_admin.py" doctor
