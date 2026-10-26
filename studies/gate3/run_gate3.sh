#!/bin/sh
# Run Gate 3: the six comparisons and the two native audits. Results go to results/; runs and run folders go to
# $GATE3_WORK (default ../../../ABMfact-brief/gate3), outside the repository.
set -eu
cd "$(dirname "$0")"
WORK=${GATE3_WORK:-../../../ABMfact-brief/gate3}
mkdir -p results "$WORK"
for problem in monolayer sorting immune chemotaxis drug_pulse spheroid; do
    free=$(df -g "$WORK" | awk 'NR==2 {print $4}')
    if [ "$free" -lt 20 ]; then echo "stopping: only $free GB free" >&2; exit 3; fi
    echo "== $problem $(date '+%H:%M:%S')"
    abmfact compare "$problem.compare.yaml" --out "results/$problem" --store "$WORK/store" \
        --workdir "$WORK/work/$problem" || true    # exit 1 means a disagreement, which is a result
done
for audit in native/*.audit.yaml; do
    name=$(basename "$audit" .audit.yaml)
    echo "== $name $(date '+%H:%M:%S')"
    abmfact run "$audit" --out "results/native/$name" --quiet
done
echo "== done $(date '+%H:%M:%S')"
