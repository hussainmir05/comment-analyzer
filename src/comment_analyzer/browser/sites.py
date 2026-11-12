"""What the app knows about each browser site: its domains, login page and login cookie."""

from dataclasses import dataclass

from comment_analyzer.models import Platform


@dataclass(frozen=True, slots=True)
class Site:
    platform: Platform
    domains: tuple[str, ...]
    login_url: str
    home_url: str
    login_cookies: tuple[str, ...]  # present only while logged in
    terms_note: str  # shown once, before the first account for this site is added

    def owns(self, host: str) -> bool:
        """True for the site's own domains and their subdomains."""
        host = host.lower().rstrip(".")
        return any(host == d or host.endswith("." + d) for d in self.domains)


_NOTE = (
    "{name} doesn't offer an API that lets this app read other people's posts. The app reads "
    "only what {name} shows you in the app's own browser, while you're logged in, at human pace, "
    "one post at a time. It never posts, likes or comments for you, and stops if {name} asks you "
    "to check something.\n\nAutomated reading may go against {name}'s terms, and {name} could "
    "limit or block the account. Use an account you're comfortable with, and keep the daily "
    "limits in Settings low."
)

SITES = {
    Platform.FACEBOOK: Site(
        Platform.FACEBOOK,
        ("facebook.com",),
        "https://www.facebook.com/login/",
        "https://www.facebook.com/",
        ("c_user",),
        _NOTE.format(name="Facebook")
        + "\n\nFacebook allows one personal account per person: add your own account, or "
        "colleagues' accounts only with their agreement.",
    ),
    Platform.INSTAGRAM: Site(
        Platform.INSTAGRAM,
        ("instagram.com",),
        "https://www.instagram.com/accounts/login/",
        "https://www.instagram.com/",
        ("sessionid",),
        _NOTE.format(name="Instagram"),
    ),
    Platform.TIKTOK: Site(
        Platform.TIKTOK,
        ("tiktok.com",),
        "https://www.tiktok.com/login",
        "https://www.tiktok.com/",
        ("sessionid", "sid_tt"),
        _NOTE.format(name="TikTok"),
    ),
}
