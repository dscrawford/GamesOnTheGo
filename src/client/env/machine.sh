# Which machine a launch is on, for the settings that belong to the machine
# rather than to the game.
#
# Embedded in every environment's launcher (lib.nix reads this file into it),
# so a game started from the library, from Steam or from `gotg play` sees the
# same answer. Two facts, each a variable a person or a test can set first:
#
#   GOTG_MACHINE           deck | desktop. A Deck is what its firmware calls
#                          itself: Jupiter (LCD) or Galileo (OLED) in the DMI
#                          product name -- EmuDeck's rule, and the one thing
#                          that does not change with the OS image.
#   GOTG_EXTERNAL_DISPLAY  1 when a display other than the machine's own panel
#                          is connected, else 0. Read off the kernel's DRM
#                          connectors: a panel is eDP, DSI or LVDS; anything
#                          else that says "connected" is a television or a
#                          monitor. The Deck's dock shows up as a DP connector.
#
# The first user: Ryujinx on a Deck. Its default is docked mode -- 1080p for
# a 1280x800 panel, 2.25 times the pixels, on a machine that gains nothing
# when docked -- and the 60fps variants raise the emulated DRAM to 8 GiB on
# 16 GB shared with the GPU. docs/research/switch-on-deck.md.

gotg_machine_detect() {
  local dmi="${GOTG_DMI_PRODUCT_FILE:-/sys/devices/virtual/dmi/id/product_name}"
  local drm="${GOTG_DRM_DIR:-/sys/class/drm}"
  local product status
  if [ -z "${GOTG_MACHINE:-}" ]; then
    product="$(cat "$dmi" 2>/dev/null || true)"
    case "$product" in
      Jupiter | Galileo) GOTG_MACHINE=deck ;;
      *) GOTG_MACHINE=desktop ;;
    esac
  fi
  export GOTG_MACHINE
  if [ -z "${GOTG_EXTERNAL_DISPLAY:-}" ]; then
    GOTG_EXTERNAL_DISPLAY=0
    for status in "$drm"/card*-*/status; do
      [ -f "$status" ] || continue
      case "$status" in
        *-eDP-* | *-DSI-* | *-LVDS-*) continue ;;
      esac
      if [ "$(cat "$status" 2>/dev/null)" = connected ]; then
        GOTG_EXTERNAL_DISPLAY=1
      fi
    done
  fi
  export GOTG_EXTERNAL_DISPLAY
}
