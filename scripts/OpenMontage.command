#!/bin/bash
# Double-click in Finder to open OpenMontage in Terminal.
exec "$(dirname "$0")/openmontage.sh" "$@"
