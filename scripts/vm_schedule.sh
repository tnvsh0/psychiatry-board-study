#!/usr/bin/env bash
# Pause / resume the Cloud Scheduler jobs that STOP weekly-review-vm.
#
# Why this exists: four scheduled jobs shut the VM down on a timetable, which is
# fine for the books ingestion they were built for but fatal to a long unattended
# run — a Sketchy bulk render across 144 chapters spans days, and a stop mid-run
# kills it. This is what was actually behind "the VM keeps auto-terminating": it
# was never crashing, it was being switched off on schedule.
#
# Only the STOP jobs are touched. The START jobs stay enabled — an extra start
# during a run is harmless, and leaving them on keeps the books workflow intact.
#
#   ./vm_schedule.sh pause    # before a bulk run
#   ./vm_schedule.sh resume   # after it finishes  <-- DO NOT FORGET
#   ./vm_schedule.sh status
set -u

PROJECT=psych-research-agent
LOCATION=us-central1
STOP_JOBS=(
  psychiatry-weekly-review-stop-wed
  psychiatry-books-stop-satnight
  psychiatry-books-stop-weekday
  psychiatry-weekly-review-stop
)

case "${1:-status}" in
  pause|resume)
    for j in "${STOP_JOBS[@]}"; do
      printf '%-38s ' "$j"
      gcloud scheduler jobs "$1" "$j" --project "$PROJECT" --location "$LOCATION" 2>&1 | tail -1
    done
    ;;
  status) ;;
  *) echo "usage: $0 {pause|resume|status}" >&2; exit 2 ;;
esac

echo
gcloud scheduler jobs list --project "$PROJECT" --location "$LOCATION" \
  --format="table(name.basename(),schedule,state)"
