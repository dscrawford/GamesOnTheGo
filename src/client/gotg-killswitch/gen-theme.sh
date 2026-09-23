#!/bin/sh
# theme.h from config/theme.yaml: the picker's seat colours and the three
# colours the bar is drawn in, so the overlay and the picker cannot drift.
# They did, once: a copy of the palette was the fifth, and nothing would have
# said so. Usage: gen-theme.sh <theme.yaml> > theme.h   (needs yq)
set -eu
theme="$1"
triple() {
  # [18, 18, 20] -> the three numbers, space-separated
  yq -o=json -I=0 "$1" "$theme" | tr -d '[] ' | tr ',' ' '
}
rgb() {
  # shellcheck disable=SC2046  # the three numbers are meant to split
  set -- $(triple "$2") "$1"
  printf '#define GS_THEME_%s_R %s\n#define GS_THEME_%s_G %s\n#define GS_THEME_%s_B %s\n' "$4" "$1" "$4" "$2" "$4" "$3"
}
echo '// Generated from config/theme.yaml by gen-theme.sh. Do not edit.'
echo '#ifndef GOTG_THEME_H'
echo '#define GOTG_THEME_H'
printf '#define GS_THEME_PLAYERS %s\n' "$(yq -o=json -I=0 '.players' "$theme" | tr '[]' '{}')"
rgb BACKGROUND '.colours.background'
rgb TEXT '.colours.text'
rgb TEXT_DIM '.colours.text_dim'
echo '#endif'
