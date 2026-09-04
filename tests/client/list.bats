#!/usr/bin/env bats
# `gotg list` — searching and paginating the catalog.
#
# A real library runs to hundreds of entries, so the two things worth testing
# are that a search finds what you meant and that a truncated list never reads
# as though it were the whole thing.

bats_require_minimum_version 1.5.0

load helper

setup() {
  setup_env
  start_saves_service
  write_api_config
}

teardown() { stop_saves_service; }

# A catalog large enough to be cut off, plus a few known names to search for.
big_catalog() {
  local i
  for i in $(seq 1 60); do
    add_manifest_entry n64 "/Games/n64/usa.filler_$i.z64" file 100 "" "Filler $i"
  done
  add_manifest_entry n64 /Games/n64/usa.legend_of_zelda_majoras_mask.z64 \
    file 100 "" "Majora's Mask"
  add_manifest_entry snes /Games/snes/world.super_metroid.sfc file 100 "" "Super Metroid"
}

@test "a bare list stops at 50 and says what it left out" {
  big_catalog
  gotg list
  [ "$status" -eq 0 ]
  # 50 rows, plus the header.
  [ "$(grep -c "usa\.\|world\." <<<"$output")" -eq 50 ]
  [[ "$stderr" == *"showing 1-50 of 62 (page 1 of 2)"* ]]
  [[ "$stderr" == *"--all"* ]]
}

@test "the truncation footer offers the next page" {
  big_catalog
  gotg list
  [[ "$stderr" == *"gotg list 2"* ]]
}

@test "a numeric positional is the page" {
  big_catalog
  gotg list 2
  [ "$status" -eq 0 ]
  [ "$(grep -c "usa\.\|world\." <<<"$output")" -eq 12 ]
  [[ "$stderr" == *"showing 51-62 of 62 (page 2 of 2)"* ]]
}

@test "a page past the end says how many pages there are" {
  big_catalog
  gotg list 9
  [ "$status" -eq 0 ]
  [[ "$stderr" == *"only 2 page"* ]]
}

@test "--platform lists only that platform" {
  big_catalog
  gotg list --platform snes
  [ "$status" -eq 0 ]
  [[ "$output" == *"world.super_metroid"* ]]
  [ "$(grep -c "n64" <<<"$output")" -eq 0 ]
}

@test "--platform composes with a pattern" {
  big_catalog
  gotg list --platform n64 metroid
  [ "$status" -eq 0 ]
  [[ "$stderr" == *"nothing matches"* ]]
  gotg list --platform snes metroid
  [[ "$output" == *"world.super_metroid"* ]]
}

@test "pages compose with --platform and --limit" {
  big_catalog
  gotg list --platform n64 --limit 10 2
  [ "$status" -eq 0 ]
  [ "$(grep -c "usa\." <<<"$output")" -eq 10 ]
  [[ "$stderr" == *"page 2 of 7"* ]]
  # The hint must keep every filter, or "the next page" is a different list.
  [[ "$stderr" == *"gotg list --platform n64 --limit 10 3"* ]]
}

@test "an unknown platform names the ones that exist" {
  big_catalog
  gotg list --platform vectrex
  [ "$status" -eq 0 ]
  [[ "$stderr" == *"no games on platform 'vectrex'"* ]]
  [[ "$stderr" == *"n64"* ]]
  [[ "$stderr" == *"snes"* ]]
}

@test "--all and a page cannot combine" {
  big_catalog
  gotg list --all 2
  [ "$status" -ne 0 ]
  [[ "$stderr" == *"--all"* ]]
}

@test "bad list arguments are each rejected with a clear message" {
  big_catalog
  local case args msg
  local -a cases=(
    "0|a page starts at 1"
    "007|a page starts at 1"
    "1 2|usage: gotg list"
    "--limit x|--limit takes a number"
    "--limit=|--limit takes a number"
    "--platform=|--platform needs a name"
  )
  for case in "${cases[@]}"; do
    args="${case%%|*}"
    msg="${case##*|}"
    # shellcheck disable=SC2086
    gotg list $args
    [ "$status" -ne 0 ]
    [[ "$stderr" == *"$msg"* ]] || {
      echo "gotg list $args: expected *${msg}* in: $stderr" >&2
      false
    }
  done
}

@test "--platform matches without case, in either spelling" {
  big_catalog
  local spelling
  for spelling in "--platform snes" "--platform SNES" "--platform=snes" "--platform=SNES"; do
    # shellcheck disable=SC2086
    gotg list $spelling
    [ "$status" -eq 0 ]
    [[ "$output" == *"world.super_metroid"* ]]
    [ "$(grep -c "usa\." <<<"$output")" -eq 0 ]
  done
}

@test "a total exactly divisible by the limit ends cleanly on its last page" {
  big_catalog
  gotg list --limit 31 2
  [ "$status" -eq 0 ]
  [ "$(grep -c "usa\.\|world\." <<<"$output")" -eq 31 ]
  [[ "$stderr" == *"showing 32-62 of 62 (page 2 of 2)"* ]]
  [[ "$stderr" != *"the next page"* ]]
}

@test "a limit of one pages a row at a time to the exact end" {
  big_catalog
  gotg list --limit 1 62
  [ "$status" -eq 0 ]
  [ "$(grep -c "usa\.\|world\." <<<"$output")" -eq 1 ]
  [[ "$stderr" == *"showing 62-62 of 62 (page 62 of 62)"* ]]
  gotg list --limit 1 63
  [ "$status" -eq 0 ]
  [[ "$stderr" == *"only 62 page"* ]]
}

@test "a filter that narrows below the requested page says past-the-end" {
  big_catalog
  gotg list --platform snes metroid 2
  [ "$status" -eq 0 ]
  [[ "$stderr" == *"only 1 page"* ]]
}

@test "a pattern that misses on a real platform names both" {
  big_catalog
  gotg list --platform snes filler
  [ "$status" -eq 0 ]
  [[ "$stderr" == *"nothing matches filler on snes"* ]]
}

@test "an unknown platform is unknown in any case" {
  big_catalog
  gotg list --platform VECTREX
  [ "$status" -eq 0 ]
  [[ "$stderr" == *"no games on platform 'VECTREX'"* ]]
  [[ "$stderr" == *"snes"* ]]
}

@test "a leading-zero limit is rejected, not read as octal" {
  big_catalog
  local bad
  for bad in 08 010; do
    gotg list --limit "$bad"
    [ "$status" -ne 0 ]
    [[ "$stderr" == *"--limit takes a number"* ]]
    [[ "$stderr" != *"value too great for base"* ]]
  done
}

@test "an astronomically large page does not overflow into sed" {
  big_catalog
  gotg list 9223372036854775808
  [[ "$stderr" != *"sed:"* ]]
  [[ "$stderr" == *"page"* ]]
  gotg list 18446744073709551618
  [[ "$stderr" != *"showing 51-62"* ]]
}

@test "the next-page hint survives being pasted back" {
  big_catalog
  gotg list 'filler [0-9]' --limit 20
  [ "$status" -eq 0 ]
  local hint
  hint="$(grep -F 'the next page' <<<"$stderr")"
  hint="${hint%%#*}"
  hint="${hint#*gotg }"
  eval "gotg $hint"
  [ "$status" -eq 0 ]
  [[ "$stderr" == *"page 2 of 3"* ]]
}

@test "--all prints every one" {
  big_catalog
  gotg list --all
  [ "$status" -eq 0 ]
  [ "$(grep -c "usa\.\|world\." <<<"$output")" -eq 62 ]
  [[ "$stderr" != *"showing"* ]]
}

@test "--limit takes a number, in either spelling" {
  big_catalog
  gotg list --limit 3
  [ "$(grep -c "usa\." <<<"$output")" -eq 3 ]
  gotg list --limit=7
  [ "$(grep -c "usa\." <<<"$output")" -eq 7 ]
}

@test "a pattern searches the id" {
  big_catalog
  gotg list majoras_mask
  [ "$status" -eq 0 ]
  [[ "$output" == *"usa.legend_of_zelda_majoras_mask"* ]]
  [ "$(grep -c "usa\.\|world\." <<<"$output")" -eq 1 ]
}

@test "a pattern searches the title and the platform too" {
  big_catalog
  gotg list "Super Metroid"
  [[ "$output" == *"world.super_metroid"* ]]
  gotg list snes
  [[ "$output" == *"world.super_metroid"* ]]
  [ "$(grep -c "world\." <<<"$output")" -eq 1 ]
}

@test "matching ignores case" {
  big_catalog
  gotg list MAJORAS
  [[ "$output" == *"majoras_mask"* ]]
}

@test "a game matching in two fields at once is still listed once" {
  # "metroid" is in both the id and the title. Naively this prints twice.
  big_catalog
  gotg list metroid
  [ "$(grep -c "world\.super_metroid" <<<"$output")" -eq 1 ]
}

@test "it really is a regex, not a substring" {
  big_catalog
  gotg list '^world\.'
  [ "$(grep -c "world\." <<<"$output")" -eq 1 ]
  [[ "$output" != *"usa."* ]]
}

@test "a pattern that matches nothing says so rather than printing a header" {
  big_catalog
  gotg list nosuchgame
  [ "$status" -eq 0 ]
  [[ "$stderr" == *"nothing matches nosuchgame"* ]]
  [[ "$output" != *"PLATFORM"* ]]
}

@test "a broken regex is reported, not passed through as a jq error" {
  big_catalog
  gotg list '('
  [ "$status" -ne 0 ]
  [[ "$stderr" == *"not a valid pattern"* ]]
}

@test "a search narrow enough to fit is not marked as truncated" {
  big_catalog
  gotg list majoras
  [[ "$stderr" != *"showing"* ]]
}

@test "two patterns is a mistake worth naming" {
  big_catalog
  gotg list one two
  [ "$status" -ne 0 ]
  [[ "$stderr" == *"usage: gotg list"* ]]
}

@test "the 007 refusal points at the regex spelling" {
  big_catalog
  gotg list 007
  [ "$status" -ne 0 ]
  [[ "$stderr" == *"00[7]"* ]]
}

@test "--search and --page are the named spellings of the positionals" {
  big_catalog
  gotg list --search metroid
  [ "$status" -eq 0 ]
  [[ "$output" == *"world.super_metroid"* ]]

  gotg list --page 2
  [ "$status" -eq 0 ]
  [[ "$stderr" == *"page 2 of 2"* ]]
}

@test "the three named flags compose in any order" {
  big_catalog
  gotg list --page 1 --search filler --platform n64
  [ "$status" -eq 0 ]
  [[ "$output" == *"usa.filler_"* ]]
  [ "$(grep -c "world\." <<<"$output")" -eq 0 ]
}

@test "a slot named twice, in either spelling, is a mistake" {
  big_catalog
  gotg list zelda --search metroid
  [ "$status" -ne 0 ]
  [[ "$stderr" == *"usage: gotg list"* ]]
  gotg list 2 --page 3
  [ "$status" -ne 0 ]
  [[ "$stderr" == *"usage: gotg list"* ]]
}

@test "--search finds a game whose name is a number" {
  add_manifest_entry n64 /Games/n64/usa.turok_1997.z64 file 100 "" "Turok 1997"
  gotg refresh
  gotg list --search 1997
  [ "$status" -eq 0 ]
  [[ "$output" == *"usa.turok_1997"* ]]
}

@test "--page rejects a bad value like the positional does" {
  big_catalog
  gotg list --page 0
  [ "$status" -ne 0 ]
  [[ "$stderr" == *"a page starts at 1"* ]]
  gotg list --page x
  [ "$status" -ne 0 ]
  [[ "$stderr" == *"a page starts at 1"* ]]
}

@test "the named flags are exactly equivalent to the positionals" {
  big_catalog
  local pair positional named a
  for pair in \
    "metroid|--search metroid" \
    "majora|--search majora" \
    "2|--page 2"; do
    positional="${pair%%|*}"
    named="${pair##*|}"
    # shellcheck disable=SC2086
    gotg list $positional
    a="$output"
    # shellcheck disable=SC2086
    gotg list $named
    [ "$output" = "$a" ] || {
      echo "list $positional != list $named" >&2
      false
    }
  done
}

@test "an empty --search is refused, in either spelling" {
  big_catalog
  gotg list --search ''
  [ "$status" -ne 0 ]
  [[ "$stderr" == *"--search needs a pattern"* ]]
  gotg list --search=
  [ "$status" -ne 0 ]
  [[ "$stderr" == *"--search needs a pattern"* ]]
}

@test "a named flag missing its value does not swallow the next flag" {
  big_catalog
  gotg list --search --platform nes
  [ "$status" -ne 0 ]
  [[ "$stderr" == *"--search needs a pattern"* ]]
  gotg list --page --all
  [ "$status" -ne 0 ]
  [[ "$stderr" == *"--page needs a number"* ]]
}

# --- --installed ---------------------------------------------------------------

@test "--installed shows only what is here, and pages over that" {
  big_catalog
  mkdir -p "$GOTG_GAMES_DIR/n64"
  printf 'rom' >"$GOTG_GAMES_DIR/n64/usa.legend_of_zelda_majoras_mask.z64"
  gotg list --installed
  [ "$status" -eq 0 ]
  [[ "$output" == *"usa.legend_of_zelda_majoras_mask"* ]]
  [[ "$output" != *"usa.filler_1 "* ]]
  [ "$(grep -c "usa\.\|world\." <<<"$output")" -eq 1 ]
  [[ "$stderr" == *"showing only these"* ]]
}

@test "--installed with nothing here says so rather than printing a header" {
  big_catalog
  gotg list --installed
  [ "$status" -eq 0 ]
  [[ "$stderr" == *"nothing installed here"* ]]
  [[ "$output" != *"PLATFORM"* ]]
}

@test "--installed rides along to the next page" {
  local i
  for i in $(seq 1 60); do
    add_manifest_entry n64 "/Games/n64/usa.filler_$i.z64" file 100 "" "Filler $i"
  done
  mkdir -p "$GOTG_GAMES_DIR/n64"
  for i in $(seq 1 55); do printf 'rom' >"$GOTG_GAMES_DIR/n64/usa.filler_$i.z64"; done
  gotg list --installed
  [[ "$stderr" == *"showing 1-50 of 55"* ]]
  [[ "$stderr" == *"gotg list --installed 2"* ]]
}
