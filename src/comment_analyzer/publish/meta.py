"""Publishing a card to a Facebook Page and its linked Instagram account, through Meta's Graph API.

Set up once in Settings, under Meta: a Page access token from the user's own Meta developer app,
with pages_manage_posts, pages_read_engagement, instagram_basic and instagram_content_publish.
The token lives in Windows Credential Manager. Nothing is sent until the user clicks Publish and
confirms.

- Facebook Page: the card goes to /{page-id}/photos with the caption.
- Instagram: the Content Publishing API fetches the image from a public address, and the app has
  none. So the card is first uploaded to the Page as an unpublished photo, which never shows on
  the Page, and Instagram gets that photo's address. The container is published once Instagram
  has processed it, then the unpublished photo is deleted.
"""

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx2

from comment_analyzer.errors import UserError

log = logging.getLogger(__name__)

GRAPH = "https://graph.facebook.com/v23.0"
READY_CHECKS = 20  # Instagram processing: up to 20 checks, 3 seconds apart
READY_PAUSE = 3.0


class MetaError(UserError):
    pass


@dataclass(frozen=True, slots=True)
class MetaTarget:
    page_id: str
    page_name: str
    instagram_id: str | None = None
    instagram_name: str | None = None


class MetaClient:
    def __init__(
        self,
        token: str,
        *,
        transport: httpx2.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._token = token
        self._client = httpx2.Client(
            base_url=GRAPH, timeout=httpx2.Timeout(60.0, connect=15.0), transport=transport
        )
        self._sleep = sleep

    def close(self) -> None:
        self._client.close()

    def check(self) -> MetaTarget:
        """Which Page the token belongs to, and its Instagram account if one is linked."""
        me = self._call(
            "GET", "/me", params={"fields": "id,name,instagram_business_account{id,username}"}
        )
        instagram = me.get("instagram_business_account") or {}
        if not me.get("id"):
            raise MetaError("Meta didn't say which Page this token is for.")
        return MetaTarget(
            str(me["id"]),
            str(me.get("name") or me["id"]),
            str(instagram["id"]) if instagram.get("id") else None,
            str(instagram.get("username") or "") or None,
        )

    def publish_page_photo(self, page_id: str, image: Path, caption: str) -> str:
        """Returns the new post's ID."""
        answer = self._upload(page_id, image, {"message": caption, "published": "true"})
        return str(answer.get("post_id") or answer["id"])

    def publish_instagram(self, target: MetaTarget, image: Path, caption: str) -> str:
        """Returns the new Instagram media ID."""
        if not target.instagram_id:
            raise MetaError("No Instagram account is linked to this Page.")
        photo = str(self._upload(target.page_id, image, {"published": "false"})["id"])
        try:
            images = self._call("GET", f"/{photo}", params={"fields": "images"}).get("images") or []
            if not images:
                raise MetaError("Facebook didn't return an address for the card.")
            url = max(images, key=lambda i: i.get("width", 0))["source"]
            container = str(
                self._call(
                    "POST",
                    f"/{target.instagram_id}/media",
                    data={"image_url": url, "caption": caption},
                )["id"]
            )
            self._wait_until_ready(container)
            published = self._call(
                "POST", f"/{target.instagram_id}/media_publish", data={"creation_id": container}
            )
            return str(published["id"])
        finally:
            try:
                self._call("DELETE", f"/{photo}")
            except MetaError:
                log.warning("Couldn't delete the unpublished helper photo %s", photo)

    def _wait_until_ready(self, container: str) -> None:
        for _ in range(READY_CHECKS):
            status = self._call("GET", f"/{container}", params={"fields": "status_code"})
            code = status.get("status_code")
            if code == "FINISHED":
                return
            if code in ("ERROR", "EXPIRED"):
                raise MetaError("Instagram couldn't process the card. Try again later.")
            self._sleep(READY_PAUSE)
        raise MetaError("Instagram took too long to process the card. Try again later.")

    def _upload(self, page_id: str, image: Path, data: dict[str, str]) -> dict[str, Any]:
        with image.open("rb") as f:
            return self._call(
                "POST",
                f"/{page_id}/photos",
                data=data,
                files={"source": (image.name, f, "image/jpeg")},
            )

    def _call(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        params = dict(kwargs.pop("params", {}))
        params["access_token"] = self._token
        try:
            response = self._client.request(method, path, params=params, **kwargs)
        except httpx2.TransportError as e:
            raise MetaError("Couldn't reach Facebook. Check your internet connection.") from e
        try:
            body = response.json()
        except ValueError:
            body = {}
        if response.status_code == 200 and "error" not in body:
            return body if isinstance(body, dict) else {}
        raise _error(response.status_code, body.get("error") or {})


def _error(status: int, error: dict[str, Any]) -> MetaError:
    """Meta's error codes, in plain words. The details go to the log (never the token)."""
    code, subcode = error.get("code"), error.get("error_subcode")
    log.warning("Graph API error %s: code %s/%s %s", status, code, subcode, error.get("message"))
    if code == 190:
        return MetaError(
            "The Meta token has expired or was revoked. Make a new one and save it in Settings."
        )
    if code in (10, 200, 3) or (isinstance(code, int) and 200 <= code < 300):
        return MetaError(
            "The Meta token lacks a permission this needs (pages_manage_posts, or "
            "instagram_content_publish for Instagram)."
        )
    if code in (4, 17, 32, 613):
        return MetaError("Meta is limiting requests right now. Try again in an hour.")
    if code == 9004 or code == 36003:
        return MetaError("Instagram couldn't use the card image. Try again.")
    return MetaError(
        f"Meta refused the request (error {code or status}). The details are in the log."
    )
