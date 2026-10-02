Output docs/demos/gotg-search.gif

Set Shell "bash"
Set FontSize 11
Set Width 980
Set Height 300
Set Padding 10
Set TypingSpeed 30ms
Set Framerate 12

# The demo shell, off camera: a plain prompt, and `gotg` in the flake
# registry naming a library (docs/demos/README.md). Sleeps rather than
# Wait+Screen throughout -- these answer from the lock in a second or two,
# and vhs 0.11's Wait never matches against a prompt it did not set itself.
Hide
Type "docs/demos/demo-shell.sh"
Enter
Sleep 2s
Type "clear"
Enter
Sleep 500ms
Show

Type "nix search gotg majora"
Enter
Sleep 5s

Type "nix run gotg#ui -- --list --search zelda --platform n64"
Enter
Sleep 6s
