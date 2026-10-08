"""The IGDB access token: minted on demand, kept until nearly out.

Its own file so that the one lock and the one exchange with Twitch read
together, away from the handler's routing. (It is not tokens.py at the package
root: that is the per-person credential store, a different thing.)
"""

from __future__ import annotations

import json
import threading
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

from .._http import NO_REDIRECT_OPENER
from .config import TIMEOUT, USER_AGENT, Config

# Refresh a token with less than this left. IGDB issues them for about sixty
# days, so a day of slack costs nothing and removes the race where a token
# expires between the check and the upstream call.
REFRESH_MARGIN = 24 * 60 * 60


@dataclass
class TokenCache:
    """The IGDB access token, minted on demand and kept until it is nearly out.

    Holding this is the reason the service exists rather than each client doing
    its own exchange — and the lock is the reason three simultaneous requests on
    a cold start mint one token rather than three.
    """

    value: str = ""
    expires_at: float = 0.0
    lock: threading.Lock = field(default_factory=threading.Lock)

    def get(self, config: Config) -> str:
        with self.lock:
            if self.value and time.time() < self.expires_at - REFRESH_MARGIN:
                return self.value

            body = urllib.parse.urlencode(
                {
                    "client_id": config.igdb_client_id,
                    "client_secret": config.igdb_client_secret,
                    "grant_type": "client_credentials",
                }
            ).encode()
            request = urllib.request.Request(
                config.igdb_token_url,
                data=body,
                headers={"User-Agent": USER_AGENT},
            )
            with NO_REDIRECT_OPENER.open(request, timeout=TIMEOUT) as response:  # noqa: S310
                payload = json.loads(response.read())

            self.value = payload["access_token"]
            self.expires_at = time.time() + float(payload.get("expires_in", 0))
            return self.value
