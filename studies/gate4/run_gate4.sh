#!/bin/sh
# Run Gate 4: calibrate the four mapping gains, then the six frozen Gate 3 comparisons on fresh seeds with those
# gains, then the same runs with PhysiCell as baseline. Results go to results/; runs and run folders go to
# $GATE4_WORK (default ../../../ABMfact-brief/gate4), outside the repository. See PROTOCOL.md.
set -eu
cd "$(dirname "$0")"
WORK=${GATE4_WORK:-../../../ABMfact-brief/gate4}
mkdir -p results "$WORK"
space() {
    free=$(df -g "$WORK" | awk 'NR==2 {print $4}')
    if [ "$free" -lt 20 ]; then echo "stopping: only $free GB free" >&2; exit 3; fi
}
# 1. Calibration. A failed calibration stops the study: no comparison runs without its gains.
for t in pressure_compucell3d pressure_morpheus motility_compucell3d motility_morpheus; do
    space
    echo "== calibration $t $(date '+%H:%M:%S')"
    abmfact translate "calibration/$t.translate.yaml" --store "$WORK/store" --workdir "$WORK/work/calibration/$t" \
        --out "results/calibration/$t" --quiet
done
# 2. Judging: the six comparisons, cheapest first.
for problem in monolayer immune sorting chemotaxis drug_pulse spheroid; do
    space
    echo "== $problem $(date '+%H:%M:%S')"
    abmfact compare "$problem.compare.yaml" --out "results/$problem" --store "$WORK/store" \
        --workdir "$WORK/work/$problem" || true    # exit 1 means a disagreement, which is a result
done
# 3. PhysiCell as baseline: the same runs, served by the store.
for problem in monolayer immune sorting chemotaxis drug_pulse spheroid; do
    echo "== physicell baseline $problem $(date '+%H:%M:%S')"
    abmfact compare "physicell_baseline/$problem.compare.yaml" --out "results/physicell_baseline/$problem" \
        --store "$WORK/store" --workdir "$WORK/work/physicell_baseline/$problem" || true
done
echo "== done $(date '+%H:%M:%S')"
