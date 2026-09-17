#!/usr/bin/env bats
# `gotg admin import`: the importer, now rather than Sunday.
#
# Everything here runs against a stubbed kubectl — the command's whole job is
# to drive it correctly, and a test that needed a cluster would never run.

bats_require_minimum_version 1.5.0

load helper

setup() {
  setup_env
  export KUBECTL_LOG="$TEST_TMP/kubectl.log"
  export GOTG_KUBECTL="$TEST_TMP/bin/kubectl"
  mkdir -p "$TEST_TMP/bin"
}

# A kubectl that records its argv and answers like the real one. Modes:
#   ok       create/wait succeed, logs replay a real import's interesting lines
#   failed   wait times out and the job reports a failure
#   hung     wait times out and the job reports nothing
stub_kubectl() {
  local mode="${1:-ok}"
  # A literal bash path: the nix build sandbox has no /usr/bin/env, which is
  # the same reason cmd-steam.sh calls python3 by name rather than shebang.
  cat >"$GOTG_KUBECTL" <<EOF
#!$(command -v bash)
echo "\$*" >>"$KUBECTL_LOG"
mode="$mode"
EOF
  cat >>"$GOTG_KUBECTL" <<'EOF'
case "$1" in
  create)
    # `create job --dry-run=client -o json` answers with the cloned spec, for
    # a caller that edits it; `create -f -` takes the edited one on stdin.
    if [[ "$*" == *"--dry-run=client"* ]]; then
      echo '{"kind":"Job","spec":{"template":{"spec":{"containers":[{"name":"importer","args":["--scan","/data/Torrents"]}]}}}}'
    elif [[ "$*" == *"-f -"* ]]; then
      cat >"$KUBECTL_LOG.manifest"
    fi
    exit 0
    ;;
  wait) [ "$mode" = ok ] && exit 0 || exit 1 ;;
  get)
    # jsonpath {.status.failed}
    [ "$mode" = failed ] && echo 1
    exit 0
    ;;
  logs)
    cat <<'LOGS'
INFO scanned /data/Torrents: 42 game source(s), 12 entry(s) ignored
WARNING a game set is going unimported: Fukutake Publishing - StudyBox — 13 zipped ROMs but directory name is unmapped; add it to rules.yaml dat_dirs
WARNING manual review: /data/Torrents/Some Game (WiiU decrypt deferred)
INFO noop usa.zelda -> /data/Games/n64/usa.zelda.z64
INFO games-root pass: 2650 published, 0 error(s)
INFO hardlink=12 extract=0 archive=0 manual=1 skip=3 unchanged=3024 error=0
LOGS
    exit 0
    ;;
esac
exit 0
EOF
  chmod +x "$GOTG_KUBECTL"
}

@test "a run clones the cronjob, waits, and reports the lines a person acts on" {
  stub_kubectl ok
  gotg admin import
  [ "$status" -eq 0 ]

  # The Job is cloned from the CronJob — the same pod spec Sunday gets — under
  # a timestamped name, since Job names are unique and finished Jobs linger.
  grep -qE '^create job gotg-import-manual-[0-9]{8}-[0-9]{6} --from=cronjob/gotg-import$' "$KUBECTL_LOG"
  grep -qE '^wait --for=condition=complete job/gotg-import-manual-.* --timeout=1800s$' "$KUBECTL_LOG"

  # What was flagged reaches the operator; the noop flood does not.
  [[ "$stderr" == *"StudyBox"* ]]
  [[ "$stderr" == *"manual review"* ]]
  [[ "$output" != *"noop usa.zelda"* ]]

  # The summary is the stdout payload, INFO prefix shed.
  [[ "$output" == *"hardlink=12 extract=0 archive=0 manual=1 skip=3 unchanged=3024 error=0"* ]]
  [[ "$output" == *"games-root pass: 2650 published, 0 error(s)"* ]]
  [[ "$stderr" == *"gotg refresh"* ]]
}

@test "a failed job is an error that names where the full log lives" {
  stub_kubectl failed
  gotg admin import
  [ "$status" -ne 0 ]
  [[ "$stderr" == *"the import failed"* ]]
  [[ "$stderr" == *"kubectl logs job/gotg-import-manual-"* ]]
}

@test "a job still running at the timeout is patience, not failure" {
  stub_kubectl hung
  gotg admin import --timeout 5
  [ "$status" -ne 0 ]
  [[ "$stderr" == *"still running after 5s"* ]]
  [[ "$stderr" == *"kubectl logs -f"* ]]
  grep -q -- "--timeout=5s" "$KUBECTL_LOG"
}

@test "--timeout takes seconds and nothing else" {
  stub_kubectl ok
  gotg admin import --timeout abc
  [ "$status" -ne 0 ]
  [[ "$stderr" == *"--timeout takes seconds"* ]]
}

@test "an unknown option is refused, not a silently different import" {
  stub_kubectl ok
  gotg admin import --now
  [ "$status" -ne 0 ]
  [[ "$stderr" == *"unknown option for import"* ]]
}

@test "no kubectl is a plain missing-command error, before anything runs" {
  export GOTG_KUBECTL="$TEST_TMP/bin/definitely-absent"
  gotg admin import
  [ "$status" -ne 0 ]
  [[ "$stderr" == *"required command not found: kubectl"* ]]
}

@test "import needs no admin token and no service at all" {
  # The cluster plane, not the service's: whoever can run kubectl can already
  # do everything this does. No GOTG_ADMIN_TOKEN, no api.json, no server.
  stub_kubectl ok
  GOTG_ADMIN_TOKEN="" gotg admin import
  [ "$status" -eq 0 ]
}

@test "the cronjob name can be overridden for a differently-named deployment" {
  stub_kubectl ok
  GOTG_IMPORT_CRONJOB=my-importer gotg admin import
  [ "$status" -eq 0 ]
  grep -q -- "--from=cronjob/my-importer" "$KUBECTL_LOG"
}

@test "import is in the admin help" {
  gotg admin help
  [[ "$output" == *"import"* ]]
  [[ "$output" == *"--follow"* ]]
}

@test "--match narrows the run to sources whose name matches, on the same pod spec" {
  # A full scan is twenty minutes; the one update just dropped in is seconds.
  # The clone is still the CronJob's spec -- the same image and mounts -- with
  # the match appended to the importer's own arguments.
  stub_kubectl ok
  gotg admin import --match "Breath of the Wild"
  [ "$status" -eq 0 ]
  grep -qE '^create job gotg-import-manual-[0-9]{8}-[0-9]{6} --from=cronjob/gotg-import --dry-run=client -o json$' "$KUBECTL_LOG"
  grep -qE '^create -f -$' "$KUBECTL_LOG"
  [ "$(jq -r '.spec.template.spec.containers[0].args | join(" ")' "$KUBECTL_LOG.manifest")" = "--scan /data/Torrents --match Breath of the Wild" ]
  grep -qE '^wait --for=condition=complete job/gotg-import-manual-' "$KUBECTL_LOG"
}

@test "--match takes a pattern" {
  stub_kubectl ok
  gotg admin import --match
  [ "$status" -ne 0 ]
  [[ "$stderr" == *"--match takes"* ]]
}
