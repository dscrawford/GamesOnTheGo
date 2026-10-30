# The static web frontend, as a directory a reverse proxy serves (or the
# service mounts) at /. Until the frontend lands this is a placeholder page, so
# the flake output, the image and the deployment can all be wired and checked
# now. The frontend's package replaces `frontend` below -- nothing else here
# changes. A flake sees only tracked files, so `web/dist` must be committed or
# produced by a derivation, never just built in the checkout.
{
  lib,
  runCommand,
  frontend ? null,
}:

runCommand "gotg-web-0.1.0"
  {
    passthru.version = "0.1.0";
    meta.description = "The GOTG web app's static frontend";
  }
  (
    if frontend != null then
      ''
        cp -r ${frontend} $out
      ''
    else
      ''
        mkdir -p $out
        cat > $out/index.html <<'HTML'
        <!doctype html><meta charset="utf-8"><title>GOTG</title>
        <p>The GOTG web app is not built into this image yet.</p>
        HTML
      ''
  )
