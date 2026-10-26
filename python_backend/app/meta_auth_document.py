"""Recognize Meta authentication routes and actual HTML login forms.

Return URLs and script templates are not authentication responses. This module
only classifies a received document; it cannot restore or submit a session.
"""
from html.parser import HTMLParser
import re
from urllib.parse import urlsplit


def _route(url):
    try:
        parts = urlsplit(str(url or ""))
        host = (parts.hostname or "").casefold()
    except ValueError:
        return "", ""
    if host != "facebook.com" and not host.endswith(".facebook.com"):
        return "", ""
    return host, parts.path.casefold()


def auth_route_gate(url):
    host, path = _route(url)
    if not host:
        return ""
    if re.search(r"(?:^|/)checkpoint(?:\.php)?(?:/|$)", path):
        return "CHECKPOINT_REQUIRED"
    if host == "business.facebook.com" and (
            path == "/business/loginpage" or path.startswith("/business/loginpage/")):
        return "BUSINESS_LOGIN_GATE"
    if re.search(r"(?:^|/)login(?:page|\.php)?(?:/|$)", path):
        return "SESSION_EXPIRED"
    return ""


class _LoginForms(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.found = False
        self.inert = []

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "template", "textarea", "title"}:
            self.inert.append(tag)
        if tag != "form" or self.inert:
            return
        attrs = dict(attrs)
        if str(attrs.get("id") or "").casefold() == "login_form":
            self.found = True
            return
        try:
            target = urlsplit(attrs.get("action") or "")
            host = (target.hostname or "").casefold()
        except ValueError:
            return
        # An action's query/fragment cannot turn a settings form into login.
        if host and host != "facebook.com" and not host.endswith(".facebook.com"):
            return
        path = target.path.casefold()
        self.found |= path in {"/login", "/login.php"} or path.startswith("/login/")

    def handle_endtag(self, tag):
        if self.inert and tag == self.inert[-1]:
            self.inert.pop()


def has_login_form(body):
    parser = _LoginForms()
    parser.feed(str(body or ""))
    return parser.found


def auth_gate(url, body):
    gate = auth_route_gate(url)
    if gate:
        return gate
    return "SESSION_EXPIRED" if _route(url)[0] and has_login_form(body) else ""
