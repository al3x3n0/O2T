#!/bin/sh
# A deliberately broken "solver" that answers `sat` to everything -- used to prove the
# cross-checker catches a second solver that DISAGREES with z3 on a proved obligation.
cat >/dev/null
echo sat
