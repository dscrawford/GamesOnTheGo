# Compiles the parts of the kill switch and its overlay that hold no SDL --
# the decision, the /proc reading, who is joining, where the bar is, what a
# frame draws and what padmap's lines say -- with their unit tests. Warnings
# are errors: the combination is three booleans and an unsigned subtraction,
# and the compiler catches more of what can go wrong there than a test can.
{ pkgs }:

let
  src = ../../src/client/gotg-killswitch;
in
pkgs.runCommand "check-killswitch"
  {
    nativeBuildInputs = [
      pkgs.gcc
      pkgs.pkg-config
      pkgs.yq-go
    ];
    buildInputs = [ pkgs.cjson ];
  }
  ''
    cp ${src}/*.c ${src}/*.h ${src}/gen-theme.sh .
    sh gen-theme.sh ${../../config/theme.yaml} > theme.h
    cp ${../../tests/client/killswitch_test.c} killswitch_test.c
    cp ${../../tests/client/overlay_test.c} overlay_test.c
    cc -O1 -std=c17 -Wall -Wextra -Werror -I. -o killswitch-test \
      killswitch_test.c killswitch.c procstat.c -lm
    ./killswitch-test
    cc -O1 -std=c17 -Wall -Wextra -Werror -I. -o overlay-test \
      overlay_test.c pairing.c bar.c shapes.c scene.c events.c padlink.c frame.c \
      $(pkg-config --cflags --libs libcjson) -lm
    ./overlay-test
    touch $out
  ''
