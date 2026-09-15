"""Bounded, transient Gmail MIME text with credential values removed."""

from __future__ import annotations

import base64
import binascii
import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from email.errors import HeaderParseError
from email.header import decode_header, make_header
from email.message import Message
from html import unescape
from html.parser import HTMLParser
from urllib.parse import parse_qsl, unquote, urlsplit, urlunsplit

MAX_SOURCE_TEXT_CHARS = 4000
MAX_MIME_PARTS = 64
MAX_MIME_DEPTH = 8
MAX_ENCODED_PART_CHARS = 131072
MAX_EXTRACTED_CHARS = 65536
REDACTED = "[자격정보삭제]"

_OTP_LABEL = r"(?:인증\s*(?:번호|코드)|확인\s*코드|보안\s*(?:코드|번호)|로그인\s*(?:인증\s*)?(?:번호|코드)|(?:로그인|인증)(?:에|을|를)?\s*(?:필요한|사용할|사용하는|위한)\s*(?:번호|코드)|일회[용성]\s*(?:비밀번호|암호|코드|번호)|(?:verification|security|authentication|confirmation|login|log[- ]?in|sign[- ]?in)\s*code|(?:code|passcode)\s+(?:to|for)\s+(?:sign[- ]?in|log[- ]?in)|one[- ]time\s*(?:password|passcode|code)|otp)"
_NUMBER_UNIT = r"(?:년|월|일|시|분|초|원|달러|개|명|건|minutes?\b|mins?\b|seconds?\b|secs?\b|hours?\b|KRW\b|USD\b|EUR\b)"
_OTP_DIGITS = rf"(?!\d{{4}}[ \t]*[-/.][ \t]*\d{{1,2}}[ \t]*[-/.][ \t]*\d{{1,2}})\d{{1,8}}(?!\d)(?![ \t]*{_NUMBER_UNIT})"
_OTP_NUMBER = rf"{_OTP_DIGITS}(?:[ \t\r\n-]+{_OTP_DIGITS}){{0,7}}"
_OTP_CONTEXT = re.compile(_OTP_LABEL, re.IGNORECASE)
_OTP_LINE = re.compile(
    rf"^[ \t]*(?P<value>{_OTP_NUMBER})[ \t]*$", re.IGNORECASE | re.MULTILINE
)
_OTP_AFTER = re.compile(
    rf"(?P<label>{_OTP_LABEL})[\s:：=\-·]*(?:는|은|이|가|is\b)?[\s:：=\-·]*[\[(]?(?P<value>{_OTP_NUMBER})",
    re.IGNORECASE,
)
_OTP_BEFORE = re.compile(
    rf"(?<!\d)(?P<value>{_OTP_NUMBER})(?P<label>\s*(?:(?:[은는이가]\s*|is\s+(?:your\s+|the\s+)?)(?:[^\W\d_][\w.'-]{{0,31}}\s+){{0,3}})?\s*{_OTP_LABEL})",
    re.IGNORECASE,
)
_CREDENTIAL_LABEL = r"(?:(?:password[ _-]*)?reset[ _-]*(?:token|secret)|magic[ _-]*link[ _-]*(?:token|secret)|비밀번호\s*(?:재설정|초기화)\s*(?:토큰|비밀값)|비밀\s*번호|임시\s*암호|암호|아이디|(?:학교|학내|로그인)\s*계정|계정\s*(?:이메일|아이디|id)|password|passwd|pwd|pw|passcode|api[ _-]*key|client[ _-]*secret|access[ _-]*token|refresh[ _-]*token|authorization|username|user[ _-]*name|login[ _-]*(?:id|email)|id)"
_PAIR = re.compile(
    r"(?im)(?P<label>(?:아이디|(?<![A-Za-z])ID)\s*(?:[/+&]|및)\s*(?:비밀번호|PW|password))\s*(?:[:=：]|는|은)\s*(?P<values>[^\n]+)"
)
_PAIR_VALUES = re.compile(
    r"\s*(?P<first>[^\s,;<>/|]+)(?:(?:[ \t]*(?:[/|]|와|과|및|and\b)[ \t]*|[ \t]+)(?P<second>[^\s,;<>]+))?",
    re.IGNORECASE,
)
_PAIR_FACT = re.compile(
    r"^(?:(?:비용|금액|가격|삭제예정일|기한|날짜)(?:은|는|이|가|[:=]|\d)|계정정보(?:가|는)|비밀번호(?:가|는)|변경(?:되었습니다|됐습니다|되지)|\d+\s*(?:원|분|년|월|일)|\d{4}-\d{1,2}-\d{1,2})"
)
_FIELD = re.compile(
    rf"(?im)(?P<label>(?<![A-Za-z0-9_]){_CREDENTIAL_LABEL})\s*(?P<delimiter>:|=|：|는|은)\s*(?P<value>[^\s,;<>]+)"
)
_SPACED_FIELD = re.compile(
    rf"(?im)(?P<label>(?<![A-Za-z0-9_]){_CREDENTIAL_LABEL})(?:[ \t]+|\n[ \t]*)(?P<value>[!-~][^\s,;<>]*)"
)
_LABEL_LINE = re.compile(rf"(?i)^\s*{_CREDENTIAL_LABEL}\s*[:=]?\s*$")
_PAIR_LINE = re.compile(
    r"(?i)^\s*(?:아이디|ID)\s*(?:[/+&]|및)\s*(?:비밀번호|PW|password)\s*[:=]?\s*$"
)
_KEY = re.compile(
    r"(?<![A-Za-z0-9_-])(?:AKIA[A-Z0-9]{16}|ASIA[A-Z0-9]{16}|sk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{16,}|gh[opsu]_[A-Za-z0-9]{20,}|AIza[A-Za-z0-9_-]{20,}|xox[baprs]-[A-Za-z0-9-]{10,})(?![A-Za-z0-9_-])"
)
_JWT = re.compile(
    r"(?<![A-Za-z0-9_-])eyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+(?![A-Za-z0-9_-])"
)
_URL = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)
_AUTH_LINK = re.compile(
    r"(?P<label>(?:magic[- ]?link|매직\s*링크|로그인\s*링크|비밀번호\s*재설정\s*(?:링크|주소)|password\s*reset\s*(?:link|url))\s*(?:[:=：]|is\b)?\s*)(?P<url>https?://[^\s<>\"']+)",
    re.IGNORECASE,
)
_AUTH_PATH = re.compile(
    r"/(?:auth|login|log-in|signin|sign-in|magic(?:[-_/]?(?:link|login))?|reset(?:[-_/]?password)?|password[-_/]reset|verify(?:[-_]?email)?)(?:/|$)",
    re.IGNORECASE,
)
_AUTH_QUERY = re.compile(
    r"(?:token|password|passwd|pwd|secret|signature|credential|assertion|authorization|saml|jwt|session|oauth|auth|reset|verify|login|ticket|api.?key|x-amz-|x-goog-)",
    re.IGNORECASE,
)


def _redact_url(match: re.Match[str]) -> str:
    value = match.group(0)
    url = value.rstrip(".,;:!?)]}")
    punctuation = value[len(url) :]
    try:
        parsed = urlsplit(url)
        keys = [
            key
            for key, _ in parse_qsl(
                parsed.query, keep_blank_values=True, max_num_fields=128
            )
        ]
        fragment_keys = [
            key
            for key, _ in parse_qsl(
                parsed.fragment, keep_blank_values=True, max_num_fields=128
            )
        ]
        sensitive = any(
            _AUTH_QUERY.search(key)
            or key.casefold() in {"code", "otp", "key", "sig", "state", "sid"}
            for key in [*keys, *fragment_keys]
        )
        if parsed.username is not None or parsed.password is not None:
            return "[인증 링크 삭제]" + punctuation
        auth_path = _AUTH_PATH.search(unquote(parsed.path))
        auth_fragment = _AUTH_PATH.search("/" + unquote(parsed.fragment).lstrip("/"))
        if auth_fragment or (auth_path and parsed.fragment):
            return "[인증 링크 삭제]" + punctuation
        if auth_path:
            tail = unquote(parsed.path)[auth_path.end() :]
            if any(
                re.fullmatch(r"(?:[A-Za-z0-9_.~%=-]{8,}|\d{4,8})", segment)
                for segment in tail.split("/")
            ):
                return "[인증 링크 삭제]" + punctuation
        if sensitive or (auth_path and parsed.query):
            return (
                urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))
                + "[인증 쿼리 삭제]"
                + punctuation
            )
        return value
    except (ValueError, UnicodeError):
        return "[인증 링크 삭제]" + punctuation


def _is_otp_value(value: str) -> bool:
    if not 4 <= sum(character.isdigit() for character in value) <= 8:
        return False
    return re.fullmatch(r"\d{4}[-/.]\d{1,2}[-/.]\d{1,2}", value.strip()) is None


def redact_display_credentials(text: str, *, context: str | None = None) -> str:
    """Use English display markers without changing persisted source normalization."""
    return (
        redact_credentials(text.replace("[REDACTED]", REDACTED), context=context)
        .replace(REDACTED, "[REDACTED]")
        .replace("[인증 링크 삭제]", "[link_removed]")
        .replace("[인증 쿼리 삭제]", "[query_removed]")
    )


def _redact_pair_values(text: str) -> tuple[str, int]:
    """Consume at most the two values named by an explicit ID/PW label."""
    match = _PAIR_VALUES.match(text)
    if match is None or match["first"] == REDACTED:
        return text, 0
    if match["second"] is not None and not _PAIR_FACT.match(match["second"]):
        return REDACTED + text[match.end() :], 2
    if _PAIR_FACT.match(match["first"]):
        return text, 0
    return REDACTED + text[match.end("first") :], 1


def _redact_pair_field(match: re.Match[str]) -> str:
    text, consumed = _redact_pair_values(match["values"])
    return match["label"] + ": " + text if consumed else match.group(0)


def redact_credentials(text: str, *, context: str | None = None) -> str:
    """Redact recognized secrets in source or display text, without logging input.

    An optional title/snippet context enables redaction of standalone OTP lines;
    ordinary numbers without an authentication context are preserved.
    """
    text = unicodedata.normalize("NFKC", unescape(text))
    text = "".join(
        char
        for char in text
        if char in "\n\t" or not unicodedata.category(char).startswith("C")
    )
    text = _AUTH_LINK.sub(lambda match: match["label"] + "[인증 링크 삭제]", text)
    text = _URL.sub(_redact_url, text)
    text = _JWT.sub(REDACTED, text)
    text = _KEY.sub(REDACTED, text)
    text = _OTP_AFTER.sub(
        lambda match: (
            match["label"] + " " + REDACTED
            if _is_otp_value(match["value"])
            else match.group(0)
        ),
        text,
    )
    text = _OTP_BEFORE.sub(
        lambda match: (
            REDACTED + match["label"]
            if _is_otp_value(match["value"])
            else match.group(0)
        ),
        text,
    )
    # A subject can establish OTP context while the body holds only the digits.
    # Restrict that context-only fallback to standalone numeric lines, not prose.
    if _OTP_CONTEXT.search(text) or (
        context
        and _OTP_CONTEXT.search(unicodedata.normalize("NFKC", unescape(context)))
    ):
        text = _OTP_LINE.sub(
            lambda match: REDACTED if _is_otp_value(match["value"]) else match.group(0),
            text,
        )
    lines = text.splitlines()
    pending = 0
    for index, line in enumerate(lines):
        if not line.strip():
            continue
        if _PAIR_LINE.fullmatch(line):
            pending = 2
        elif _LABEL_LINE.fullmatch(line):
            pending = max(pending, 1)
        elif pending:
            if pending == 2:
                lines[index], consumed = _redact_pair_values(line)
                pending = pending - consumed if consumed else 0
            elif re.fullmatch(r"\s*[^\s]{1,256}\s*", line):
                lines[index] = REDACTED
                pending -= 1
            else:
                pending = 0
    text = "\n".join(lines)
    text = _PAIR.sub(_redact_pair_field, text)
    text = _FIELD.sub(
        lambda match: (
            match["label"] + ": " + REDACTED
            if match["delimiter"] in {":", "=", "："}
            or (
                not re.fullmatch(
                    r"\d{1,4}(?:년|월|일|시|분)(?:부터|까지|마다|에)?", match["value"]
                )
                and (
                    any(character.isdigit() for character in match["value"])
                    or any(character in match["value"] for character in "@_/=+")
                )
            )
            else match.group(0)
        ),
        text,
    )
    # A prose verb after "password" is not a credential value. Whitespace-only
    # fields need an unmistakable value shape or a short password field label.
    text = _SPACED_FIELD.sub(
        lambda match: (
            match["label"] + " " + REDACTED
            if match["label"].casefold() in {"pw", "pwd"}
            or any(character.isdigit() for character in match["value"])
            or any(character in match["value"] for character in "@_/=+")
            else match.group(0)
        ),
        text,
    )
    return text


class _VisibleHTML(HTMLParser):
    _VOID = frozenset(
        {
            "area",
            "base",
            "br",
            "col",
            "embed",
            "hr",
            "img",
            "input",
            "link",
            "meta",
            "param",
            "source",
            "track",
            "wbr",
        }
    )
    _HIDDEN = frozenset(
        {
            "script",
            "style",
            "head",
            "template",
            "noscript",
            "svg",
            "iframe",
            "object",
            "canvas",
        }
    )
    _BLOCK = frozenset(
        {
            "p",
            "div",
            "br",
            "li",
            "tr",
            "td",
            "th",
            "h1",
            "h2",
            "h3",
            "h4",
            "table",
            "section",
            "hr",
        }
    )

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.hidden: list[str] = []
        self.fragments: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        styles = {
            name.strip().casefold(): value.split("!", 1)[0].strip().casefold()
            for declaration in (attributes.get("style") or "").split(";")
            if ":" in declaration
            for name, value in [declaration.split(":", 1)]
        }
        hidden = (
            bool(self.hidden)
            or tag in self._HIDDEN
            or "hidden" in attributes
            or (attributes.get("aria-hidden") or "").strip().casefold() == "true"
            or styles.get("display") == "none"
            or styles.get("visibility") in {"hidden", "collapse"}
            or styles.get("mso-hide") == "all"
            or styles.get("opacity") in {"0", "0.0"}
        )
        if hidden:
            if tag not in self._VOID:
                self.hidden.append(tag)
        elif tag in self._BLOCK:
            self.fragments.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if self.hidden:
            if tag in self.hidden:
                index = len(self.hidden) - 1 - self.hidden[::-1].index(tag)
                del self.hidden[index:]
        elif tag in self._BLOCK:
            self.fragments.append("\n")

    def handle_data(self, data: str) -> None:
        if not self.hidden:
            self.fragments.extend((data, " "))


def _headers(part: Mapping[str, object]) -> dict[str, str]:
    headers = part.get("headers", [])
    if not isinstance(headers, list):
        return {}
    result = {}
    for header in headers[:64]:
        if (
            isinstance(header, Mapping)
            and isinstance(header.get("name"), str)
            and isinstance(header.get("value"), str)
        ):
            result.setdefault(header["name"].casefold(), header["value"])
    return result


def _normalize_text(text: str) -> str:
    return re.sub(r"\n{3,}", "\n\n", re.sub(r"[^\S\n]+", " ", text)).strip()


@dataclass(frozen=True, slots=True)
class MailSourceText:
    text: str | None
    source_content: str
    truncated: bool


def extract_mail_source(
    payload: object, snippet: str | None, *, max_chars: int = MAX_SOURCE_TEXT_CHARS
) -> MailSourceText:
    """Read bounded inline bodies; completeness belongs to the selected MIME branch."""
    if type(max_chars) is not int or not 1 <= max_chars <= MAX_EXTRACTED_CHARS:
        raise ValueError("Invalid mail source text limit")
    visited = 0

    def walk(part: object, depth: int) -> tuple[str, bool]:
        nonlocal visited
        if not isinstance(part, Mapping):
            return "", True
        if depth > MAX_MIME_DEPTH or visited >= MAX_MIME_PARTS:
            return "", True
        visited += 1
        headers = _headers(part)
        disposition = (
            headers.get("content-disposition", "").split(";", 1)[0].strip().casefold()
        )
        if part.get("filename") or disposition == "attachment":
            return "", False
        mime = str(part.get("mimeType", "")).casefold().split(";", 1)[0].strip()
        if mime.startswith("multipart/"):
            parts = part.get("parts", [])
            if not isinstance(parts, list) or not parts:
                return "", True
            children = parts[:MAX_MIME_PARTS]
            if mime == "multipart/alternative":
                # Alternatives represent the same body. Do not spend extraction
                # capacity on HTML that a complete plain representation replaces.
                children = sorted(
                    children,
                    key=lambda child: (
                        not (
                            isinstance(child, Mapping)
                            and str(child.get("mimeType", ""))
                            .casefold()
                            .split(";", 1)[0]
                            == "text/plain"
                        )
                    ),
                )
                fallback = ""
                incomplete = len(parts) > MAX_MIME_PARTS
                for child in children:
                    text, missing = walk(child, depth + 1)
                    if text.strip() and not missing:
                        return text, False
                    if not fallback and text.strip():
                        fallback = text
                    incomplete |= missing
                return fallback, incomplete
            fragments: list[str] = []
            length = 0
            incomplete = len(parts) > MAX_MIME_PARTS
            for child in children:
                text, missing = walk(child, depth + 1)
                incomplete |= missing
                if text:
                    available = MAX_EXTRACTED_CHARS - length
                    fragments.append(text[:available])
                    length += len(text) + 1
                    if length > MAX_EXTRACTED_CHARS:
                        return "\n".join(fragments)[:MAX_EXTRACTED_CHARS], True
            return "\n".join(fragments), incomplete
        if mime not in {"text/plain", "text/html"}:
            return "", False
        body = part.get("body")
        if not isinstance(body, Mapping) or body.get("attachmentId"):
            return "", True
        data = body.get("data")
        if not isinstance(data, str) or not data:
            return "", body.get("size") != 0
        if len(data) > MAX_ENCODED_PART_CHARS:
            return "", True
        incomplete = False
        try:
            raw = base64.b64decode(
                data + "=" * (-len(data) % 4), altchars=b"-_", validate=True
            )
            message = Message()
            message["Content-Type"] = headers.get("content-type", mime)
            charset = message.get_content_charset() or "utf-8"
            try:
                text = raw.decode(charset)
            except (LookupError, UnicodeError):
                incomplete = True
                text = raw.decode("utf-8", errors="replace")
        except (ValueError, binascii.Error):
            return "", True
        if "size" in body and body["size"] != len(raw):
            incomplete = True
        if mime == "text/html":
            parser = _VisibleHTML()
            parser.feed(text)
            parser.close()
            text = "".join(parser.fragments)
        return text[:MAX_EXTRACTED_CHARS], incomplete or len(text) > MAX_EXTRACTED_CHARS

    body, truncated = walk(payload, 0)
    subject = (
        _headers(payload).get("subject", "") if isinstance(payload, Mapping) else ""
    )
    try:
        subject = str(make_header(decode_header(subject)))
    except (HeaderParseError, LookupError, UnicodeError):
        pass
    context = subject + "\n" + (snippet or "")
    body_text = _normalize_text(redact_credentials(body, context=context))
    snippet_text = _normalize_text(redact_credentials(snippet or "", context=subject))
    if body_text:
        text = body_text
        if snippet_text and snippet_text not in body_text:
            if len(snippet_text) > 500:
                truncated = True
            text = "미리보기: " + snippet_text[:500] + "\n본문: " + body_text
        source = "body"
    else:
        text = snippet_text
        source = "snippet_only"
    if len(text) > max_chars:
        truncated = True
    return MailSourceText(text[:max_chars] or None, source, truncated)
