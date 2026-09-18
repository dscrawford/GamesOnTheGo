# Which input devices one copy of a co-op game may see.
#
# Every copy runs in a sandbox where /dev/input holds only its own player's
# controller -- the trick SplitScreenWrapper took from PartyDeck -- because
# a game maps whatever gamepad it finds first, and four copies would all
# find the same one. The controller is padmap's clone for that seat,
# "padmap Player N", which is what every pad is once gotg has seated it.
#
# A copy with no clone of its own gets *nothing*, never everything. The
# first version left such a copy unsandboxed, seeing every pad including
# player one's clone, and one controller drove both players. Without padmap
# running there is no telling pads apart, so copy one sees them all -- one
# player, and the pad they are holding -- and the others get the keyboard.
{ pkgs }:
pkgs.writeShellScript "gotg-coop-seat" ''
  n="$1"
  sys="''${GOTG_INPUT_SYS:-/sys/class/input}"
  jq=${pkgs.jq}/bin/jq

  # /dev/input/eventN for the device with this name, if there is one.
  named() {
    local f
    for f in "$sys"/event*/device/name; do
      [ -f "$f" ] || continue
      if [ "$(cat "$f")" = "$1" ]; then
        printf '/dev/input/%s\n' "$(basename "$(dirname "$(dirname "$f")")")"
        return 0
      fi
    done
    return 1
  }

  padmap=0
  for f in "$sys"/event*/device/name; do
    [ -f "$f" ] || continue
    case "$(cat "$f")" in "padmap Player "*) padmap=1 ;; esac
  done

  if [ "$padmap" = 1 ]; then
    if dev="$(named "padmap Player $n")"; then
      $jq -nc --arg d "$dev" '{ devices: [$d], isolate: true }'
    else
      $jq -nc '{ devices: [], isolate: true }'
    fi
  elif [ "$n" = 1 ]; then
    $jq -nc '{ devices: [], isolate: false }'
  else
    $jq -nc '{ devices: [], isolate: true }'
  fi
''
