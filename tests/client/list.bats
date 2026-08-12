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
