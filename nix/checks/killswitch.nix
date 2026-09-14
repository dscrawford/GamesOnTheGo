# Compiles the decision and the /proc reading with their unit tests — the two
# halves of this feature that hold no SDL (see killswitch.h, procstat.h).
# Warnings are errors: the combination is three booleans and an unsigned
# subtraction, and the compiler catches more of what can go wrong there than a
# test can.
{ pkgs }:

pkgs.runCommand "check-killswitch" { nativeBuildInputs = [ pkgs.gcc ]; } ''
  cp ${../../src/client/gotg-killswitch/killswitch.c} killswitch.c
  cp ${../../src/client/gotg-killswitch/killswitch.h} killswitch.h
  cp ${../../src/client/gotg-killswitch/procstat.c} procstat.c
  cp ${../../src/client/gotg-killswitch/procstat.h} procstat.h
  cp ${../../tests/client/killswitch_test.c} killswitch_test.c
  cc -O1 -std=c17 -Wall -Wextra -Werror -o killswitch-test killswitch_test.c killswitch.c procstat.c
  ./killswitch-test
  touch $out
''
