# The shell the recorded demos run in.
#
# The catalog never ages out here: a demo that reaches for the server records
# a ten-second DNS timeout and three warnings before its first line of output.
# Completion is sourced from the checkout rather than the installed package so
# the recording matches the tree it was made from.

export GOTG_MANIFEST_MAX_AGE=99999999
PS1='$ '

source "$(dirname "${BASH_SOURCE[0]}")/../../src/client/completions/gotg.bash"

# One Tab lists the alternatives instead of waiting for a second one, and no
# terminal bell — a flash on every ambiguous completion reads as an error.
bind 'set show-all-if-ambiguous on'
bind 'set bell-style none'
