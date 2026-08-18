Output docs/demos/gotg-complete.gif

Require gotg

Set Shell "bash"
Set FontSize 11
Set Width 980
Set Height 190
Set Padding 10
Set TypingSpeed 45ms
Set Framerate 12

Hide
Type "docs/demos/demo-shell.sh"
Enter
Sleep 2s
Type "clear"
Enter
Sleep 500ms
Show

# Ctrl+C rather than Enter, every time: `gotg play` launches a game, and a
# recorder that presses Enter here films an emulator. Ctrl+U would end the line
# too, but it erases what was completed — the one thing worth seeing.
Type "gotg play world.legend_of_zelda"
Sleep 700ms
Tab
Sleep 2500ms
Ctrl+C

Type "gotg play usa.super_mario_sunshine "
Sleep 700ms
Tab
Sleep 2500ms
Ctrl+C

Type "gotg steam add metroid"
Sleep 700ms
Tab
Sleep 3s
Ctrl+C
Sleep 1s
