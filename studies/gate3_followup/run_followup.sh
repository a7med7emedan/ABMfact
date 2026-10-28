#!/bin/sh
# Run the Gate 3 follow-up: three comparisons on fresh seeds. Results go to results/; runs and run folders go to
# $GATE3_WORK (default ../../../ABMfact-brief/gate3), the Gate 3 store, outside the repository.
set -eu
cd "$(dirname "$0")"
WORK=${GATE3_WORK:-../../../ABMfact-brief/gate3}
mkdir -p results "$WORK"
for problem in immune sorting drug_pulse; do
    free=$(df -g "$WORK" | awk 'NR==2 {print $4}')
    if [ "$free" -lt 20 ]; then echo "stopping: only $free GB free" >&2; exit 3; fi
    echo "== $problem $(date '+%H:%M:%S')"
    abmfact compare "$problem.compare.yaml" --out "results/$problem" --store "$WORK/store" \
        --workdir "$WORK/work/followup_$problem" || true    # exit 1 means a disagreement, which is a result
done
echo "== done $(date '+%H:%M:%S')"
