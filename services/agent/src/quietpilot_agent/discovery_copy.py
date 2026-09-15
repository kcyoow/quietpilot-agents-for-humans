"""Conservative language and time-phrasing checks for discovery display text.

These are heuristics, not a language/calendar parser or a usefulness verdict.
Source-backed names, quoted source text, URLs and code are preserved. Checks never
rewrite model output and errors contain fixed categories only.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence
from typing import Literal

_HANGUL = re.compile(r"[가-힣]")
_KANA = re.compile(r"[ぁ-ゖゝ-ゟァ-ヺヽ-ヿｦ-ﾝ]")
_JAPANESE_TOKEN = re.compile(r"[ぁ-ゖゝ-ゟァ-ヺヽ-ヿｦ-ﾝ一-鿿々〆ー]+")
_TECHNICAL = re.compile(
    r"`[^`\n]+`|(?:[A-Za-z][A-Za-z0-9+.-]*:)[^\s<>\"'“”]+"
    r"|[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}"
)
_IDENTIFIER = re.compile(r"[A-Za-z0-9]+(?:[_./:#=+-][A-Za-z0-9]+)+")
_ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}(?:T[\d:.+-]+Z?)?")
_QUOTES = re.compile(
    r'"([^"\n]+)"|“([^”\n]+)”|‘([^’\n]+)’|「([^」\n]+)」|『([^』\n]+)』'
    # Korean particles attach directly to closing quotes (e.g. 'source'라고).
    # Keep the word boundary for other scripts so apostrophes are not quotes.
    r"|(?<!\w)'([^'\n]+)'(?=$|[^\w]|[가-힣])"
)
_ENGLISH_WORD = re.compile(r"[A-Za-z]+(?:['’-][A-Za-z]+)*")
_ENGLISH_AUXILIARIES = frozenset(
    {"is", "are", "was", "were", "will", "should", "must", "has", "have"}
)
_ENGLISH_IMPERATIVES = frozenset(
    {"check", "complete", "confirm", "prepare", "review", "send", "submit", "update"}
)
_NAME_PARTICLES = frozenset({"a", "an", "and", "for", "in", "of", "on", "the", "to"})
_TIME_AMOUNT = r"(?:\d{1,4}(?:\.\d+)?\s*(?:일|시간|분|주|개월)|하루|이틀|사흘|나흘|닷새|엿새|일주일|한\s*달)"
_EN_TIME_AMOUNT = r"(?:\d{1,4}(?:\.\d+)?|one|two|three|four|five|six|seven|eight|nine|ten)\s+(?:days?|hours?|minutes?|weeks?|months?)"
_DATE_ANCHOR = (
    r"(?:마감|기한|예약|행사|일정|\bdeadline\b|\bdue\s+date\b|\bappointment\b)"
)
_RELATIVE_TIME = re.compile(
    rf"(?:현재|지금)(?:\s*(?:시점|기준(?:으로)?))?\s*(?:로부터|부터|에서)?\s*(?:약|대략)?\s*{_TIME_AMOUNT}"
    rf"|앞으로\s*(?:약|대략)?\s*{_TIME_AMOUNT}"
    rf"|{_TIME_AMOUNT}\s*(?:이|가)?\s*(?:정도|밖에|채)?\s*남"
    rf"|{_TIME_AMOUNT}\s*(?:후|뒤|이내)\s*(?:에|로)?\s*{_DATE_ANCHOR}"
    rf"|{_DATE_ANCHOR}[^.!?。\n]{{0,12}}{_TIME_AMOUNT}\s*(?:후|뒤|이내)"
    rf"|\b(?:in|within|next)\s+(?:about\s+|approximately\s+)?{_EN_TIME_AMOUNT}\b"
    rf"|\b{_EN_TIME_AMOUNT}\s+(?:left|remaining|from\s+now|away)\b",
    re.IGNORECASE,
)
_MOVING_DATE = re.compile(
    r"(?:오늘|내일|모레|어제|그제)(?=$|[^가-힣]|까지|부터|이면|이라|에는|은|이|에|을)"
    r"|(?:이번|다음|지난)\s*(?:주말|주|달|월|해)(?=$|[^가-힣]|에|부터|까지|[월화수목금토일]요일)"
    r"|\b(?:today|tomorrow|yesterday|tonight|(?:this|next|last)\s+(?:week|month|year))\b",
    re.IGNORECASE,
)
_DATE_URGENCY = re.compile(
    rf"{_DATE_ANCHOR}[^.!?。\n]{{0,24}}(?:임박|촉박|다가오|앞두|얼마\s*(?:안|남지)|서둘|긴급|시급|급해|급하|soon|imminent|approaching)"
    rf"|(?:임박한|다가오는|긴급한|시급한|imminent|approaching|urgent)[^.!?。\n]{{0,16}}{_DATE_ANCHOR}",
    re.IGNORECASE,
)
_TIME_ADEQUACY = re.compile(
    r"(?:시간|기간)(?:이|은|는|가|도)?\s*(?:아직\s*|전혀\s*|그리\s*)?(?:충분|부족|넉넉|빠듯)(?:하|해|합|한|할)"
    rf"|(?:{_DATE_ANCHOR}|현재|지금|아직|남은)[^.!?。\n]{{0,30}}?여유(?:가|는|도)?\s*(?:있|없|많|적)"
    r"|\b(?:enough|sufficient|insufficient|ample|plenty\s+of|little)\s+time\s+(?:left|remaining)\b"
    r"|\b(?:remaining\s+)?time\s+(?:is|isn't|is\s+not)\s+(?:short|sufficient|insufficient|ample|enough)\b",
    re.IGNORECASE,
)
_ADEQUACY_CONDITION_PREFIX = re.compile(
    r"\bif[ \t]+(?:the[ \t]+|there[ \t]+(?:is|isn't|is[ \t]+not)[ \t]+)?$",
    re.IGNORECASE,
)
_ADEQUACY_CONDITION_END = re.compile(
    r"^(?:하(?:면|다면|지[ \t]*않(?:으면|다면))"
    r"|[한할][ \t]+경우(?:에는|엔|에)?"
    r"|(?:있|없|많|적)(?:으면|다면|을[ \t]+경우(?:에는|엔|에)?))"
    r"(?=$|[\s,;.!?。！？])"
)
_SOURCE_TIME = re.compile(
    r"(?:메일|이메일|원문|안내)[^.!?。\n]{0,20}(?:발송|수신|작성|받았|받은|보냈|보낸)[^.!?。\n]{0,12}(?:당시|시점|기준|때)"
    r"|(?:발송|수신|작성|받은|보낸)(?:일|시점)?\s*(?:당시|기준)"
    r"|\b(?:when|at\s+the\s+time)\s+the\s+(?:email|message)\s+was\s+(?:sent|received)\b"
    r"|\bat\s+the\s+(?:email|message)(?:'s)?\s+(?:send|receipt)\s+time\b",
    re.IGNORECASE,
)


class CopyLanguageError(ValueError):
    """A fixed repair category; never include generated or source text in errors."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class CopyTemporalError(CopyLanguageError):
    """A fixed category for time-dependent persisted display claims."""


def temporal_copy_prose(value: str, *, evidence_texts: Sequence[str]) -> str:
    """Mask exact source-time quotations while preserving sentence boundaries."""
    sources = tuple(_spaces(text) for text in evidence_texts if text)
    text = _TECHNICAL.sub(
        lambda match: " " * len(match.group()), unicodedata.normalize("NFC", value)
    )
    outside_quotes = _QUOTES.sub(lambda match: " " * len(match.group()), text)
    sentence_edges = ".!?。！？\n"

    def source_time_quote(match: re.Match[str]) -> str:
        quote = _spaces(next(group for group in match.groups() if group is not None))
        start = (
            max(outside_quotes.rfind(char, 0, match.start()) for char in sentence_edges)
            + 1
        )
        ends = [outside_quotes.find(char, match.end()) for char in sentence_edges]
        end = min((position for position in ends if position >= 0), default=len(text))
        if any(quote in source for source in sources) and _SOURCE_TIME.search(
            _spaces(outside_quotes[start:end])
        ):
            return " " * len(match.group())
        return match.group()

    return _QUOTES.sub(source_time_quote, text)


def validate_temporal_copy(value: str, *, evidence_texts: Sequence[str]) -> None:
    """Persist dates, not a countdown that becomes false while the card is stored.

    This deliberately does not parse calendars or reinterpret machine date fields.
    Exact source quotations may retain relative language only when their source-time
    basis is explicit in the same surrounding sentence. Durations and direct
    account-incident urgency are not themselves deadline countdowns.
    """
    prose = temporal_copy_prose(value, evidence_texts=evidence_texts)
    if _RELATIVE_TIME.search(prose) or _MOVING_DATE.search(prose):
        raise CopyTemporalError("copy_temporal_relative")
    for assessment in _TIME_ADEQUACY.finditer(prose):
        # Exempt only this explicitly conditional predicate, never its sentence.
        # Other assertions and countdowns in the instruction remain checked.
        if _ADEQUACY_CONDITION_PREFIX.search(prose[: assessment.start()]):
            continue
        if _ADEQUACY_CONDITION_END.match(prose[assessment.end() - 1 :]):
            continue
        raise CopyTemporalError("copy_temporal_adequacy")
    if _DATE_URGENCY.search(prose):
        raise CopyTemporalError("copy_temporal_urgency")


def validate_display_copy(
    value: str,
    *,
    evidence_texts: Sequence[str],
    allow_source_name: bool = False,
    language: Literal["en", "ko"] = "ko",
) -> None:
    """Validate generated prose; Korean mode preserves the legacy caller contract.

    New generators explicitly select English. Source quotations and literal action
    titles retain their original language; this never rewrites stored text.
    """
    if language not in {"en", "ko"}:
        raise ValueError("Unsupported display language")

    sources = tuple(_spaces(text) for text in evidence_texts if text)
    # Compare canonically equivalent Unicode without rewriting persisted copy.
    original = unicodedata.normalize("NFC", value).strip()
    text = _TECHNICAL.sub(" ", original)

    def source_quote(match: re.Match[str]) -> str:
        quoted = _spaces(next(group for group in match.groups() if group is not None))
        return " " if any(quoted in source for source in sources) else match.group()

    prose = _QUOTES.sub(source_quote, text)
    if allow_source_name and (
        not prose.strip()
        or not any(character.isalpha() for character in prose)
        or _IDENTIFIER.fullmatch(original)
        or _ISO_DATE.fullmatch(original)
    ):
        return

    for match in _JAPANESE_TOKEN.finditer(prose):
        token = match.group()
        if _KANA.search(token) and not any(token in source for source in sources):
            raise CopyLanguageError("copy_unexpected_kana")

    if language == "en":
        if allow_source_name and any(_spaces(original) in source for source in sources):
            return
        english_prose = _JAPANESE_TOKEN.sub(
            lambda match: (
                " "
                if any(match.group() in source for source in sources)
                else match.group()
            ),
            prose,
        )
        unsupported_script = any(
            character.isalpha()
            and not unicodedata.name(character, "").startswith("LATIN ")
            for character in english_prose
        )
        if not unsupported_script and _ENGLISH_WORD.search(english_prose):
            return
        # Preserve the established machine category; repair text states the
        # requested output language instead of changing the wire error code.
        raise CopyLanguageError("copy_korean_required")

    if _has_english_sentence(prose):
        raise CopyLanguageError("copy_korean_required")
    if _HANGUL.search(prose):
        return
    if allow_source_name and _is_source_name(prose.strip(), sources):
        return
    raise CopyLanguageError("copy_korean_required")


def _has_english_sentence(value: str) -> bool:
    # Only high-confidence isolated clauses. Names such as The Last of Us and
    # ordinary English product names in Korean prose must not trigger this check.
    for clause in re.split(r"[.!?。！？;\n]+", value):
        if _HANGUL.search(clause):
            continue
        words = [word.casefold() for word in _ENGLISH_WORD.findall(clause)]
        if len(words) >= 2 and words[0] == "please":
            return True
        if len(words) < 3:
            continue
        if any(word in _ENGLISH_AUXILIARIES for word in words[1:-1]) or (
            words[0] in _ENGLISH_IMPERATIVES
            and words[1] in {"the", "this", "your", "our"}
        ):
            return True
    return False


def _is_source_name(value: str, sources: Sequence[str]) -> bool:
    value = _spaces(value)
    if not value or not any(value in source for source in sources):
        return False
    words = _ENGLISH_WORD.findall(value)
    return bool(words) and all(
        word.casefold() in _NAME_PARTICLES
        or any(character.isupper() for character in word)
        for word in words
    )


def _spaces(value: str) -> str:
    return " ".join(unicodedata.normalize("NFC", value).split())
