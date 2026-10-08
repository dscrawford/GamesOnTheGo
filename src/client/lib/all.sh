# shellcheck shell=bash
# Every library, sourced in the order bin/gotg needs them.
#
# The one list of libraries; bin/gotg and the test helper's `load_client_libs`
# both source it, so a test loads the program that ships.
#
# Needs GOTG_LIB set. Only defines functions; nothing here runs.

# shellcheck source=lib/color.sh
source "$GOTG_LIB/color.sh"
# shellcheck source=lib/common.sh
source "$GOTG_LIB/common.sh"
# shellcheck source=lib/config.sh
source "$GOTG_LIB/config.sh"
# shellcheck source=lib/storage.sh
source "$GOTG_LIB/storage.sh"
# shellcheck source=lib/manifest.sh
source "$GOTG_LIB/manifest.sh"
# shellcheck source=lib/download.sh
source "$GOTG_LIB/download.sh"
# shellcheck source=lib/env.sh
source "$GOTG_LIB/env.sh"
# shellcheck source=lib/launcher.sh
source "$GOTG_LIB/launcher.sh"
# shellcheck source=lib/versions.sh
source "$GOTG_LIB/versions.sh"
# shellcheck source=lib/danstick.sh
source "$GOTG_LIB/danstick.sh"
# shellcheck source=lib/killswitch.sh
source "$GOTG_LIB/killswitch.sh"
# shellcheck source=lib/cmd-play.sh
source "$GOTG_LIB/cmd-play.sh"
# shellcheck source=lib/cmd-uninstall.sh
source "$GOTG_LIB/cmd-uninstall.sh"
# shellcheck source=lib/qa-analyze.sh
source "$GOTG_LIB/qa-analyze.sh"
# shellcheck source=lib/cmd-qa.sh
source "$GOTG_LIB/cmd-qa.sh"
# shellcheck source=lib/pads.sh
source "$GOTG_LIB/pads.sh"
# shellcheck source=lib/pads-dolphin.sh
source "$GOTG_LIB/pads-dolphin.sh"
# shellcheck source=lib/pads-ryujinx.sh
source "$GOTG_LIB/pads-ryujinx.sh"
# shellcheck source=lib/pads-cemu.sh
source "$GOTG_LIB/pads-cemu.sh"
# shellcheck source=lib/keys.sh
source "$GOTG_LIB/keys.sh"
# shellcheck source=lib/firmware.sh
source "$GOTG_LIB/firmware.sh"
# shellcheck source=lib/remote.sh
source "$GOTG_LIB/remote.sh"
# shellcheck source=lib/saves.sh
source "$GOTG_LIB/saves.sh"
# shellcheck source=lib/cmd-saves.sh
source "$GOTG_LIB/cmd-saves.sh"
# shellcheck source=lib/saves-history.sh
source "$GOTG_LIB/saves-history.sh"
# shellcheck source=lib/foreign-gl.sh
source "$GOTG_LIB/foreign-gl.sh"
# shellcheck source=lib/cmd-launch.sh
source "$GOTG_LIB/cmd-launch.sh"
# shellcheck source=lib/library-play.sh
source "$GOTG_LIB/library-play.sh"
# shellcheck source=lib/updates.sh
source "$GOTG_LIB/updates.sh"
# shellcheck source=lib/cmd-controllers.sh
source "$GOTG_LIB/cmd-controllers.sh"
# shellcheck source=lib/cmd-configure.sh
source "$GOTG_LIB/cmd-configure.sh"
# shellcheck source=lib/cmd-steam.sh
source "$GOTG_LIB/cmd-steam.sh"
# shellcheck source=lib/cmd-admin.sh
source "$GOTG_LIB/cmd-admin.sh"
# shellcheck source=lib/cmd-complete.sh
source "$GOTG_LIB/cmd-complete.sh"
