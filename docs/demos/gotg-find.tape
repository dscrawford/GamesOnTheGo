Output docs/demos/gotg-find.gif

Require gotg

Set Shell "bash"
Set FontSize 11
Set Width 980
Set Height 470
Set Padding 10
Set TypingSpeed 30ms
Set Framerate 12

# The demo shell, off camera: a plain prompt, and a catalog that never expires.
# Sleeps rather than Wait+Screen throughout — these commands read the cached
# catalog and answer in a fifth of a second, and vhs 0.11's Wait never matches
# against a prompt it did not set itself.
Hide
Type "docs/demos/demo-shell.sh"
Enter
Sleep 2s
Type "clear"
Enter
Sleep 500ms
Show

Type "gotg list zelda"
Enter
Sleep 4s

Type "gotg info usa.legend_of_zelda_majoras_mask"
Enter
Sleep 4s
