"""An opener that relays a redirect instead of following it.

The proxy (`service/app.py`) and the indexer's publisher (`indexer/publish.py`)
each carried this handler, for the same reason, with the same leak in mind:
urllib's default redirect handler copies every header except Content-* onto
the new request -- Authorization included, even when the Location crosses
hosts. Behind a credential-injecting proxy that is the whole failure: one
open redirect on the upstream and the key walks off to any host on the
internet (afd63b3 fixed it in the proxy; the publisher's index token had the
same exposure). A caller sees the 3xx as an `HTTPError` and can follow it
itself, without our header. Stdlib only.
"""

from __future__ import annotations

import urllib.request


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ARG002
        return None


NO_REDIRECT_OPENER = urllib.request.build_opener(NoRedirect())
