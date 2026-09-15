"""Conservative constraints derived from explicit, bounded mail source templates."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal

from .discovery_copy import temporal_copy_prose
from .models import EvidenceRecord

_ALL_IMPORTANCE = ("HIGH", "NORMAL", "LOW")
_DATASET_TAGS = frozenset({"데이터셋", "데이터세트", "dataset", "datasets"})
_SECURITY_TAGS = frozenset({"보안알림", "보안알람", "securityalert", "securityalerts"})
_NUMBER = r"(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?"
_USD = re.compile(
    rf"(?:US\s*\$|\$|(?<![A-Za-z])USD(?![A-Za-z])\s*)\s*(?P<prefix>{_NUMBER})(?!\d|[.,]\d|[eE][+\-\d])"
    rf"|(?<![A-Za-z\d.,])(?P<suffix>{_NUMBER})\s*(?:USD(?![A-Za-z])|US\s*dollars?(?![A-Za-z])|dollars?(?![A-Za-z])|달러)",
    re.IGNORECASE,
)
_FULL_DATE = re.compile(
    r"(?<!\d)(?:(\d{4})-(\d{2})-(\d{2})|(\d{4})\s*년\s*(\d{1,2})\s*월\s*(\d{1,2})\s*일)"
)
_MONTH_DAY = re.compile(r"(?<!\d)(\d{1,2})\s*월\s*(\d{1,2})\s*일")
_MONTH_NUMBERS = {
    name: index
    for index, name in enumerate(
        (
            "jan",
            "feb",
            "mar",
            "apr",
            "may",
            "jun",
            "jul",
            "aug",
            "sep",
            "oct",
            "nov",
            "dec",
        ),
        1,
    )
}
_ENGLISH_MONTH_DAY = re.compile(
    r"(?<![A-Za-z])(?P<month>Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
    r"\.?\s+(?P<day>\d{1,2})(?:st|nd|rd|th)?(?![A-Za-z0-9])"
    r"(?:(?:,\s*|\s+)(?P<year>\d{4})(?![A-Za-z0-9]))?",
    re.IGNORECASE,
)
_EVENT_WORD = re.compile(r"\b(?:hackathon|challenge|competition)\b", re.IGNORECASE)
_REGISTERED = re.compile(
    r"\byou(?:\s+have|'ve)?\s+(?:signed\s+up|registered)\s+for\s+(?:the\s+)?([^\n.!?]{3,180})",
    re.IGNORECASE,
)
_FINAL_TITLE = re.compile(
    r"\bfinal\s+call\b|\bsubmission\s+deadline\b|(?:제출|출품|접수)\s*마감",
    re.IGNORECASE,
)
_BODY_SUBMISSION_CUTOFF = re.compile(
    r"\bsubmissions?\b.{0,240}?\b(?:hard\s+cut[ -]?off|cut[ -]?off|deadline)\b"
    r"|\bsubmit\b.{0,100}?\b(?:by|before)\b",
    re.IGNORECASE,
)
_UNCERTAIN_CUTOFF = re.compile(
    r"\b(?:no|not|without|unknown|undecided|unconfirmed|tentative|TBA|TBD)\b"
    r"|\b(?:will\s+be|to\s+be|not\s+yet)\s+announced\b|미정|미확정|추후\s*(?:공지|안내)",
    re.IGNORECASE,
)
_CLOCK_ENGLISH = re.compile(
    r"(?<!\d)(\d{1,2})(?::(\d{2}))?\s*([ap])\.?m\.?(?![A-Za-z])", re.IGNORECASE
)
_CLOCK_24 = re.compile(r"(?<!\d)(\d{1,2}):(\d{2})(?::(\d{2}))?(?!\d)")
_CLOCK_KOREAN = re.compile(
    r"(?:(오전|오후)\s*)?(\d{1,2})\s*시(?!간)(?:\s*(\d{1,2})\s*분)?"
)
_TIME_ZONE = re.compile(
    r"(?<![A-Za-z])(?:UTC|GMT)(?:[+-]\d{1,2}(?::\d{2})?)?(?![A-Za-z])"
    r"|(?<![A-Za-z])(?:KST|JST|EDT|EST|PDT|PST|CET|CEST)(?![A-Za-z])"
    r"|한국\s*(?:표준\s*)?시간",
    re.IGNORECASE,
)
_RELATIVE_DAYS = re.compile(
    r"\b(\d{1,3})\s*days?\s*(?:left|remaining|to\s+go)\b", re.IGNORECASE
)
_DAY_COUNT = r"(?<!\d)(?:\d{1,3}\s*일|하루|이틀|사흘|나흘)"
_RELATIVE_SUMMARY = re.compile(
    rf"{_DAY_COUNT}(?:\s*(?:정도|가량|쯤|이|가|은|는|을|만|도|이나|밖에|아직|더|이제)){{0,4}}"
    r"\s*남(?:았|아|은|는|겨|긴|기|지|음|게|습니다)"
    rf"|(?:남은|남아\s*(?:있는|있던)|잔여)\s*(?:(?:제출|접수|출품)\s*)?"
    rf"(?:기간|시간|일수|날짜)?\s*(?:이|가|은|는|:)?\s*(?:(?:약|단|불과|겨우|고작)\s*)?{_DAY_COUNT}"
    rf"|(?:마감|기한|제출|접수|출품)(?:일)?까지(?:는|도)?\s*(?:앞으로\s*)?{_DAY_COUNT}"
    rf"|{_DAY_COUNT}\s*(?:전|후|뒤|이내|앞)(?!\s*(?:보다|과|와))"
    r"|\b\d{1,3}\s+days?\s+(?:left|remaining|remain(?:ed)?|to\s+go|away)\b"
    r"|\b(?:in|within|next)\s+\d{1,3}\s+days?\b",
    re.IGNORECASE,
)
_RECEIPT_QUALIFIER = re.compile(
    r"(?:메일|이메일|안내|알림).{0,20}(?:발송|수신|받|도착).{0,20}(?:당시|시점|기준|때)"
    r"|(?:발송|수신|받은|받았을|도착)(?:일|시점)?\s*(?:기준|당시|때)"
    r"|\b(?:when|at\s+the\s+time)\s+the\s+(?:email|message)\s+was\s+(?:sent|received)\b"
    r"|\bthe\s+(?:email|message)\b.{0,100}\bwhen\s+it\s+was\s+(?:sent|received)\b"
    r"|\bat\s+the\s+(?:email|message)(?:'s)?\s+(?:send|receipt)\s+time\b",
    re.IGNORECASE,
)
_ACCOUNT_DELETION = re.compile(
    r"(?:계정|회원(?:정보)?)\s*(?:이|가|은|는|의)?\s*(?:자동\s*|영구\s*)?(?:삭제|탈퇴|폐쇄)"
    r"|\baccount\s+(?:(?:will\s+be|scheduled\s+for|permanent)\s+)?(?:delet\w*|closure)\b"
    r"|\b(?:delete|deleting|close|closing)\s+(?:your\s+)?account\b",
    re.IGNORECASE,
)
_DELETION_DATE = re.compile(r"삭제\s*예정일\s*[:：]?\s*(\d{4}-\d{2}-\d{2})(?!\d)")
_IRREVERSIBLE = re.compile(
    r"복구\s*(?:할\s*수|가|는)?\s*(?:없|불가|불가능)|되돌릴\s*수\s*없"
    r"|irreversib\w*|cannot\s+(?:be\s+)?recover\w*|can't\s+(?:be\s+)?recover\w*",
    re.IGNORECASE,
)
_PERSONAL_DATA = r"(?:개인\s*정보|개인\s*데이터|personal\s+(?:data|information))"
_BALANCE = r"(?:잔액|balance|remaining\s+funds)"
_SOURCE_LOSS = r"(?:삭제|소멸|지워|deleted|erased|removed|forfeit\w*|lost)"
_OUTREACH = re.compile(
    r"\bcall\s+for\s+(?:speakers?|papers?)\b|(?:발표자|서포터즈)(?:\s*(?:제\s*)?\d{1,3}\s*기)?(?:를|을)?\s*모집",
    re.IGNORECASE,
)
_EVENT_OUTREACH = re.compile(
    r"(?:공개\s*)?(?:특강|멘토링|세미나).{0,24}(?:초대|참가\s*신청|참여\s*신청|수강\s*신청|안내|개최|참가자\s*모집)"
    r"|(?:소통|멘토링|네트워킹)\s*(?:DAY|데이).{0,24}개최"
    r"|(?:초대|참가\s*신청).{0,24}(?:특강|멘토링|세미나)"
    r"|\b(?:lecture|seminar|webinar|mentoring)\b.{0,40}\b(?:invitation|registration|sign\s*up)\b"
    r"|\b(?:join|invitation\s+to|register\s+for)\b.{0,40}\b(?:lecture|seminar|webinar|mentoring)\b",
    re.IGNORECASE,
)
_OUTREACH_BODY = re.compile(
    r"초대|신청|등록|참여|참가|\b(?:invited|register|join|sign\s*up)\b", re.IGNORECASE
)
_INFORMATION_TITLE = re.compile(
    r"\b(?:newsletter|digest|round[ -]?up)\b|뉴스레터|(?:시큐리티|보안)\s*레터"
    r"|새로운\s*소식|업데이트(?:를)?\s*(?:공유|모음)|(?:주간|월간)\s*(?:소식|업데이트)",
    re.IGNORECASE,
)
_INFORMATION_BODY = re.compile(
    r"\b(?:newsletter|digest|round[ -]?up|subscribe|unsubscribe)\b"
    r"|\bthis\s+(?:week|month|edition|issue)\b|\blatest\s+(?:news|research|stories|updates)\b"
    r"|\bbrief\s+summary\b|(?:새|인기)\s*주제|게시물(?:을)?\s*공유|업데이트\s*보기"
    r"|구독|이번\s*(?:주|달|호)|최신\s*(?:소식|연구|논문|뉴스)|업데이트(?:를)?\s*(?:공유|모음)",
    re.IGNORECASE,
)
_MARKETING_TITLE = re.compile(
    r"\b(?:sale|discount|promotion|special\s+(?:offer|deal))\b|할인|특가|프로모션|쿠폰|혜택\s*안내|\(광고\)",
    re.IGNORECASE,
)
_MARKETING_BODY = re.compile(
    r"\b(?:shop|buy|purchase|discount|coupon|upgrade|limited[ -]time\s+offer)\b"
    r"|쿠폰|구매|할인|업그레이드|유료\s*가입",
    re.IGNORECASE,
)
_EDUCATIONAL_PROGRAM = re.compile(
    r"\b(?:sessions?|talks?|workshops?|keynotes?)\b|세션|강연|강좌|워크숍",
    re.IGNORECASE,
)
_EDUCATIONAL_SPEAKERS = re.compile(
    r"\b(?:speakers?|presenters?)\b|발표자|연사|강사진", re.IGNORECASE
)
_EDUCATIONAL_PROMOTION = re.compile(
    r"\blearn\s+more\b|\bbuild\s+your\s+schedule\b"
    r"|\b(?:view|explore)\s+(?:the\s+)?(?:agenda|sessions?|lineup)\b"
    r"|자세히\s*보기|더\s*알아보기",
    re.IGNORECASE,
)
_EDUCATIONAL_EVENT_CONTEXT = re.compile(
    r"\b(?:sneak\s+peek|event\s+details|conference|congress|summit)\b"
    r"|(?:행사|프로그램)\s*(?:미리\s*보기|안내|정보|소개)|컨퍼런스|콘퍼런스|학술\s*대회",
    re.IGNORECASE,
)
_EDUCATIONAL_RECIPIENT_DUTY = re.compile(
    r"\byour\s+(?:assignment|submission|proposal|registration|application|training)\b.{0,60}"
    r"\b(?:due|deadline|mandatory|required|confirmed|accepted)\b"
    r"|\byou\s+(?:must|are\s+required\s+to)\s+(?:complete|submit|attend)\b"
    r"|(?:귀하|본인|회원님|수강생).{0,40}(?:필수|의무|제출|수료).{0,40}(?:마감|기한|해야|까지)",
    re.IGNORECASE,
)
_TERMS_TITLE = re.compile(
    r"\bterms(?:\s+of\s+(?:service|use))?\s+(?:update|change)\w*\b"
    r"|\b(?:updates?|changes?)\s+to\s+(?:our\s+)?terms\b|약관\s*(?:변경|개정)",
    re.IGNORECASE,
)
_TERMS_BODY = re.compile(
    r"\bterms\b.{0,80}\b(?:updated|changed|effective|take\s+effect)\b"
    r"|\b(?:updated|changed)\s+terms\b|약관.{0,80}(?:변경|개정|시행|적용)",
    re.IGNORECASE,
)
_AUTH_CODE_TITLE = re.compile(
    r"\b(?:sign[ -]?in|log[ -]?in|one[ -]time|verification|authentication|security)\s+(?:code|link|password)\b"
    r"|\b(?:sign[ -]?in|log[ -]?in)\s+to\s+(?:[\w'-]{1,40}\s+){1,4}link\b"
    r"|(?:로그인|본인\s*확인|인증)\s*(?:용\s*)?(?:코드|번호|링크)",
    re.IGNORECASE,
)
_AUTH_CODE_BODY = re.compile(
    r"\b(?:use|enter|click|follow)\b.{0,80}\b(?:code|link)\b.{0,80}\b(?:sign[ -]?in|log[ -]?in|verify|authenticate)\b"
    r"|\b(?:code|link)\b.{0,80}\b(?:expires?|valid\s+for|one[ -]time)\b"
    r"|\b(?:use|enter|copy)\b.{0,40}\bcode\b.{0,40}\bbackup\b"
    r"|(?:로그인|인증).{0,50}(?:코드|번호|링크).{0,50}(?:입력|누르|클릭|사용|유효|만료)"
    r"|(?:코드|번호|링크).{0,50}(?:입력|사용|클릭).{0,50}(?:로그인|인증)",
    re.IGNORECASE,
)
_EMAIL_CONFIRM_TITLE = re.compile(
    r"\b(?:verify|confirm)\s+(?:your\s+)?e-?mail(?:\s+address)?\b"
    r"|\be-?mail(?:\s+address)?\s+(?:verification|confirmation)\b"
    r"|이메일\s*(?:주소\s*)?(?:인증|확인)",
    re.IGNORECASE,
)
_EMAIL_CONFIRM_BODY = re.compile(
    r"\b(?:verify|confirm)\b.{0,30}\be-?mail\b.{0,80}\b(?:link|button|click)\b"
    r"|\b(?:link|button|click)\b.{0,80}\b(?:verify|confirm)\b.{0,30}\be-?mail\b"
    r"|\b(?:verify|confirm)\b.{0,30}\be-?mail\b.{0,160}\b(?:backup\s+code|code\b.{0,40}\bbackup)\b"
    r"|이메일.{0,30}(?:인증|확인).{0,60}(?:링크|버튼|클릭)"
    r"|(?:링크|버튼|클릭).{0,60}이메일.{0,30}(?:인증|확인)",
    re.IGNORECASE,
)
_IDENTITY_CODE_TITLE = re.compile(r"\bverify\s+your\s+identity\b", re.IGNORECASE)
_IDENTITY_CODE_BODY = re.compile(
    r"\b(?:verification|authentication)\s+code\b|인증\s*(?:코드|번호)", re.IGNORECASE
)
# Service names may qualify a password; only a bounded noun phrase is accepted.
_PASSWORD_NOUN = r"(?:[\w'-]{1,40}\s+){0,4}password"
_RESET_TITLE = re.compile(
    rf"\bpassword[ -]reset\b|\breset\s+(?:your\s+)?{_PASSWORD_NOUN}\b|비밀번호(?:를)?\s*(?:재설정|초기화)",
    re.IGNORECASE,
)
_RESET_BODY = re.compile(
    r"\b(?:you\s+requested|received\s+a\s+request)\b.{0,80}\b(?:reset|password)\b"
    r"|\breset\b.{0,30}\bpassword\b.{0,80}\b(?:link|button|click)\b"
    r"|\b(?:link|button|click)\b.{0,80}\breset\b.{0,30}\bpassword\b"
    r"|비밀번호.{0,30}(?:재설정|초기화).{0,60}(?:요청|링크|버튼)"
    r"|(?:링크|버튼).{0,60}비밀번호.{0,30}(?:재설정|초기화)",
    re.IGNORECASE,
)
_PASSWORD_ASSISTANCE_TITLE = re.compile(
    r"비밀번호\s*지원|\bpassword\s+assistance\b", re.IGNORECASE
)
_RESET_RECEIVED_BODY = re.compile(
    r"\b(?:received\s+(?:a|your)\s+request|you\s+requested)\b.{0,100}\b(?:reset|password)\b"
    r"|비밀번호.{0,40}(?:재설정|초기화).{0,30}요청(?:을|이)?\s*(?:받|접수)",
    re.IGNORECASE,
)
# A request's conditional safety footer is not a report that an incident occurred.
_ACCOUNT_EVENT = re.compile(
    rf"\byour\s+(?:account|credentials|{_PASSWORD_NOUN})\b.{{0,50}}\b(?:was|is|has\s+been|have\s+been)\s+(?:compromised|breached|leaked|exposed|stolen|changed|updated|reset)\b"
    rf"|\byour\s+{_PASSWORD_NOUN}\b.{{0,30}}\bfound\s+in\s+a\s+(?:data\s+)?breach\b"
    r"|\b(?:detected|noticed|blocked|identified)\b.{0,60}\b(?:suspicious|unusual|unauthorized|unrecognized)\b.{0,60}\byour\s+account\b"
    r"|\b(?:new|unrecognized)\s+(?:sign[ -]?in|log[ -]?in)\b.{0,60}\byour\s+account\b"
    r"|\b(?:suspicious|unusual|unauthorized|unrecognized)\s+(?:sign[ -]?in|log[ -]?in|access|activity)\s+(?:was\s+)?(?:detected|blocked|identified)\b"
    r"|(?:계정|비밀번호|암호).{0,40}(?:침해|탈취|유출|변경|재설정)(?:이|가)?\s*(?:되었|됐|되었습니다|됨|완료)"
    r"|(?:의심스러운|비정상|알\s*수\s*없는|승인하지\s*않은).{0,15}(?:로그인|접속|활동).{0,30}(?:감지|탐지|확인|차단)"
    r"|(?:계정|비밀번호).{0,30}(?:침해|유출).{0,20}(?:감지|확인|발견)",
    re.IGNORECASE,
)
_PASSWORD_COMPLETED = re.compile(
    r"\b(?:your\s+)?password\s+(?:has\s+been|was|is|has)\s+(?:changed|updated|reset)\b"
    r"|\bpassword\s+(?:change|reset|update)\s+(?:completed|successful)\b",
    re.IGNORECASE,
)
_SECURITY_WARNING = re.compile(
    r"(?:피싱|스미싱|사칭|사기).{0,24}(?:주의|경고|조심)"
    r"|(?:주의|경고|조심).{0,24}(?:피싱|스미싱|사칭|사기)"
    r"|보안\s*(?:알림|경고)|\bsecurity\s+(?:alert|warning)\b"
    r"|\b(?:phishing|scam|impersonation)\b.{0,24}\b(?:warning|alert|beware)\b"
    r"|\b(?:warning|alert|beware)\b.{0,24}\b(?:phishing|scam|impersonation)\b",
    re.IGNORECASE,
)
_DATASET_SOURCE = re.compile(
    r"\b(?:datasets?|corpus|corpora|parquet)\b|\btraining[\s-]+data\b"
    r"|데이터\s*(?:셋|세트)|학습\s*(?:용\s*)?데이터",
    re.IGNORECASE,
)
_THIRD_PARTY = re.compile(
    r"\bthird[\s-]+party\s+(?:oauth\s+)?(?:app|application)\b", re.IGNORECASE
)
_SOURCE_ORDINAL = re.compile(
    r"\b(?:third|3rd)\s+(?:oauth\s+)?(?:app|application)\b"
    r"|세\s*번째\s*(?:oauth\s*)?(?:앱|애플리케이션|어플리케이션)",
    re.IGNORECASE,
)
_WRONG_THIRD_PARTY = re.compile(
    r"세\s*번째\s*(?:oauth\s*)?(?:앱|애플리케이션|어플리케이션|응용\s*프로그램)"
    r"|\b(?:third|3rd)\s+(?:oauth\s+)?(?:app|application)\b",
    re.IGNORECASE,
)


def _text(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).split())


def _tag_key(value: str) -> str:
    return re.sub(r"[\s_-]+", "", _text(value).casefold())


def _sender(record: EvidenceRecord) -> str | None:
    domains = {
        fact.removeprefix("sender_domain=").casefold()
        for fact in record.facts
        if fact.startswith("sender_domain=")
    }
    return next(iter(domains)) if len(domains) == 1 else None


def _decimal(value: str) -> Decimal:
    return Decimal(value.replace(",", ""))


def _money_values(text: str) -> set[Decimal]:
    return {
        _decimal(match["prefix"] or match["suffix"]) for match in _USD.finditer(text)
    }


def _amount_field(body: str, label: str) -> Decimal | None:
    matches = re.findall(
        rf"{label}\s*:\s*\$\s*({_NUMBER})(?!\d|[.,]\d|[eE][+\-\d])", body, re.IGNORECASE
    )
    if any(sum(character.isdigit() for character in value) > 64 for value in matches):
        return None
    values = {_decimal(value) for value in matches}
    return next(iter(values)) if len(values) == 1 else None


def _exact_decimal(value: Decimal) -> str:
    # Decimal.normalize uses the ambient precision and can round large values.
    text = format(value, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def _full_date_spans(text: str) -> list[tuple[date, int, int]]:
    result = []
    for match in _FULL_DATE.finditer(text):
        values = match.groups()[:3] if match[1] else match.groups()[3:]
        try:
            result.append(
                (date(*(int(value) for value in values)), match.start(), match.end())
            )
        except ValueError:
            continue
    for match in _ENGLISH_MONTH_DAY.finditer(text):
        if match["year"] is None:
            continue
        try:
            value = date(
                int(match["year"]),
                _MONTH_NUMBERS[match["month"][:3].casefold()],
                int(match["day"]),
            )
        except ValueError:
            continue
        result.append((value, match.start(), match.end()))
    return result


def _full_dates(text: str) -> set[date]:
    return {value for value, _, _ in _full_date_spans(text)}


def _month_days(text: str) -> set[tuple[int, int]]:
    result = {(value.month, value.day) for value in _full_dates(text)}
    for month, day in _MONTH_DAY.findall(text):
        try:
            parsed = date(2000, int(month), int(day))
        except ValueError:
            continue
        result.add((parsed.month, parsed.day))
    for match in _ENGLISH_MONTH_DAY.finditer(text):
        try:
            # Leap year 2000 validates month/day only; it never becomes a source year.
            parsed = date(
                int(match["year"] or 2000),
                _MONTH_NUMBERS[match["month"][:3].casefold()],
                int(match["day"]),
            )
        except ValueError:
            continue
        result.add((parsed.month, parsed.day))
    return result


def _receipt_date_from_facts(record: EvidenceRecord) -> date | None:
    values = {
        fact.removeprefix("received_at_unix_ms=")
        for fact in record.facts
        if fact.startswith("received_at_unix_ms=")
    }
    if len(values) != 1:
        return None
    value = next(iter(values))
    if re.fullmatch(r"\d{1,16}", value) is None:
        return None
    try:
        return datetime.fromtimestamp(int(value) // 1000, UTC).date()
    except (ValueError, OverflowError, OSError):
        return None


def _metadata_date_at(summary: str, start: int, end: int) -> bool:
    before = summary[max(0, start - 40) : start]
    after = summary[end : end + 40]
    if re.match(r"\s*(?:까지|에\s*(?:제출|마감|접수)|(?:이|가)?\s*마감)", after):
        return False
    return bool(
        re.match(
            r"\s*(?:에\s*)?(?:받(?:은|았던)|수신(?:한|된)?|도착(?:한|했던)?|발송(?:한|된)?)",
            after,
        )
        or re.search(
            r"(?:수신|발송|받은|도착)(?:일|날짜|시점)?\s*(?:은|는|인|:|=)?\s*$", before
        )
    )


def _unproven_summary_date(
    summary: str,
    source_dates: frozenset[date],
    source_month_days: frozenset[tuple[int, int]],
    receipt_date: date | None,
) -> bool:
    full = _full_date_spans(summary)
    raw_full_count = len(list(_FULL_DATE.finditer(summary))) + sum(
        match["year"] is not None for match in _ENGLISH_MONTH_DAY.finditer(summary)
    )
    if raw_full_count != len(full):
        return True
    for value, start, end in full:
        if value not in source_dates and not (
            value == receipt_date and _metadata_date_at(summary, start, end)
        ):
            return True
    for match in _MONTH_DAY.finditer(summary):
        if any(start <= match.start() and match.end() <= end for _, start, end in full):
            continue
        value = (int(match[1]), int(match[2]))
        if value not in source_month_days and not (
            receipt_date is not None
            and value == (receipt_date.month, receipt_date.day)
            and _metadata_date_at(summary, match.start(), match.end())
        ):
            return True
    for match in _ENGLISH_MONTH_DAY.finditer(summary):
        if any(start <= match.start() and match.end() <= end for _, start, end in full):
            continue
        value = (_MONTH_NUMBERS[match["month"][:3].casefold()], int(match["day"]))
        if value not in source_month_days:
            return True
    return False


def source_date_supported(value: str, source_text: str) -> bool:
    """Allow equivalent explicit calendar-date notation without inferring a year."""
    if not any(
        pattern.fullmatch(value)
        for pattern in (_FULL_DATE, _MONTH_DAY, _ENGLISH_MONTH_DAY)
    ):
        return False
    return not _unproven_summary_date(
        value,
        frozenset(_full_dates(source_text)),
        frozenset(_month_days(source_text)),
        None,
    )


def _without_clock_offsets(text: str) -> str:
    text = re.sub(r"https?://\S+", " ", text, flags=re.IGNORECASE)
    text = re.sub(
        r"\b(?:UTC|GMT)\s*[+-]\d{1,2}(?::\d{2})?", " ", text, flags=re.IGNORECASE
    )
    return re.sub(
        r"(T\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?)(?:Z|[+-]\d{2}:\d{2})(?![A-Za-z0-9])",
        r"\1",
        text,
        flags=re.IGNORECASE,
    )


def _clock_zone_markers(text: str) -> frozenset[str]:
    text = re.sub(r"https?://\S+", " ", text, flags=re.IGNORECASE)
    offsets = re.findall(
        r"T\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(Z|[+-]\d{2}:\d{2})(?![A-Za-z0-9])",
        text,
        re.IGNORECASE,
    )
    return _time_zones(text) | frozenset(value.upper() for value in offsets)


def source_time_token_supported(value: str, source_text: str) -> bool:
    """Translate clock notation only; dates and zone conversions are separate."""
    value, source_text = _text(value), _text(source_text)
    if any(
        pattern.fullmatch(value)
        for pattern in (_CLOCK_ENGLISH, _CLOCK_24, _CLOCK_KOREAN)
    ):
        clocks, invalid = _clock_values(value)
        source_clocks, _ = _clock_values(_without_clock_offsets(source_text))
        return not invalid and len(clocks) == 1 and clocks.issubset(source_clocks)
    if _TIME_ZONE.fullmatch(value):
        return bool(_time_zones(value)) and _time_zones(value).issubset(
            _time_zones(source_text)
        )
    return False


def validate_source_clock_copy(value: str, source_text: str) -> None:
    """Keep represented clock values and explicit zone qualifiers source-bound."""
    clocks, invalid = _clock_values(_without_clock_offsets(value))
    source_clocks, _ = _clock_values(_without_clock_offsets(source_text))
    if invalid or not clocks.issubset(source_clocks):
        raise ValueError(
            "Preserve the source clock time without changing hours, minutes or seconds"
        )
    zones, source_zones = _clock_zone_markers(value), _clock_zone_markers(source_text)
    if not zones.issubset(source_zones) or (clocks and source_zones and not zones):
        raise ValueError(
            "Keep the explicit source time zone whenever a clock time is written"
        )


def _body_submission_cutoff(body: str) -> bool:
    for match in _BODY_SUBMISSION_CUTOFF.finditer(body):
        # A date inside the explicit submission/cutoff phrase, or immediately
        # after its deadline marker, is stronger than an unrelated event date.
        after = body[match.end() : match.end() + 120]
        clause_end = re.search(r"[!?;]|\.(?=\s+[A-Z가-힣]|$)", after)
        after = after[: clause_end.start()] if clause_end else after
        excerpt = match[0] + after
        if _UNCERTAIN_CUTOFF.search(excerpt):
            continue
        if _month_days(match[0]) or _RELATIVE_DAYS.search(match[0]):
            return True
        following = re.sub(
            r"^\s*(?:(?:is|on|at|by)\b\s*|[:—,-]\s*)*", "", after, flags=re.IGNORECASE
        )
        if (
            (
                _ENGLISH_MONTH_DAY.match(following)
                or _FULL_DATE.match(following)
                or _MONTH_DAY.match(following)
            )
            and _month_days(following)
        ) or _RELATIVE_DAYS.match(following):
            return True
    return False


def _clock_values(text: str) -> tuple[set[tuple[int, int, int]], bool]:
    values: set[tuple[int, int, int]] = set()
    occupied = []
    invalid = False
    for match in _CLOCK_ENGLISH.finditer(text):
        hour, minute = int(match[1]), int(match[2] or 0)
        if not 1 <= hour <= 12 or minute > 59:
            invalid = True
        else:
            values.add(
                (hour % 12 + (12 if match[3].casefold() == "p" else 0), minute, 0)
            )
        occupied.append((match.start(), match.end()))
    for match in _CLOCK_24.finditer(text):
        if any(
            start <= match.start() and match.end() <= end for start, end in occupied
        ):
            continue
        value = (int(match[1]), int(match[2]), int(match[3] or 0))
        if value[0] > 23 or value[1] > 59 or value[2] > 59:
            invalid = True
        else:
            values.add(value)
    for match in _CLOCK_KOREAN.finditer(text):
        hour, minute = int(match[2]), int(match[3] or 0)
        if minute > 59 or hour > 23 or (match[1] and not 1 <= hour <= 12):
            invalid = True
        else:
            if match[1]:
                hour = hour % 12 + (12 if match[1] == "오후" else 0)
            values.add((hour, minute, 0))
    return values, invalid


def _time_zones(text: str) -> frozenset[str]:
    zones = {
        "KST" if "한국" in value else value.upper()
        for value in _TIME_ZONE.findall(text)
    }
    if re.search(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?Z(?![A-Za-z])",
        text,
        re.IGNORECASE,
    ):
        zones.add("UTC")
    return frozenset(zones)


def _conditional_before(body: str, start: int) -> bool:
    return bool(
        re.search(
            r"\b(?:if|unless|when)\s*$", body[max(0, start - 20) : start], re.IGNORECASE
        )
    )


def _affirmed_match(pattern: re.Pattern[str], text: str) -> bool:
    """Accept explicit events, not conditional or negated safety instructions."""
    # A URL's host dots do not end the conditional sentence around that link.
    text = re.sub(
        r"https?://[^\s<>]+",
        lambda match: "[link]" + match[0][len(match[0].rstrip(".,;:!?)]}")) :],
        text,
        flags=re.IGNORECASE,
    )
    for clause in re.split(r"[.!?。！？;\n]", text):
        for match in pattern.finditer(clause):
            before = clause[: match.start()]
            after = clause[match.end() :]
            if re.search(
                r"\b(?:if|unless)\b|만약|(?:라면|다면|경우)", before, re.IGNORECASE
            ):
                continue
            if re.match(
                r"\s*(?:(?:다)?면|(?:된|되었을|됐을|한|했을)?\s*경우|시(?:에는|에)?(?:\s|$))",
                after,
            ):
                continue
            if re.match(
                r"\s*(?:하(?:세요|십시오|시기|라)|해\s*(?:주세요|주십시오|요))", after
            ):
                continue
            if re.search(
                r"\b(?:not|never|no)\b.{0,12}$", before, re.IGNORECASE
            ) or re.match(r"\s*(?:되지\s*않|하지\s*않|된\s*것은\s*아|없)", after):
                continue
            return True
    return False


def _reported_account_event(title: str, body: str) -> bool:
    return any(
        _affirmed_match(pattern, text)
        for pattern in (_ACCOUNT_EVENT, _PASSWORD_COMPLETED)
        for text in (title, body)
    )


def _authentication_request(title: str, body: str) -> str | None:
    if _reported_account_event(title, body):
        return None
    if _PASSWORD_ASSISTANCE_TITLE.search(title) and _affirmed_match(
        _RESET_RECEIVED_BODY, body
    ):
        return "password_reset_request"
    for name, title_pattern, body_pattern in (
        ("one_time_sign_in", _AUTH_CODE_TITLE, _AUTH_CODE_BODY),
        ("identity_verification_code", _IDENTITY_CODE_TITLE, _IDENTITY_CODE_BODY),
        ("email_confirmation", _EMAIL_CONFIRM_TITLE, _EMAIL_CONFIRM_BODY),
        ("password_reset_request", _RESET_TITLE, _RESET_BODY),
    ):
        if title_pattern.search(title) and body_pattern.search(body):
            return name
    return None


def _registered_event(title: str, body: str) -> str | None:
    for match in _REGISTERED.finditer(body):
        if _conditional_before(body, match.start()):
            continue
        event = re.split(
            r"[,;]|\s+(?:and|which|where|that)\s+",
            match[1],
            maxsplit=1,
            flags=re.IGNORECASE,
        )[0].strip(" \"'[]")
        if re.search(
            r"\b(?:newsletter|mailing\s+list|updates)\b|뉴스레터|구독",
            event,
            re.IGNORECASE,
        ):
            continue
        if not _EVENT_WORD.search(event):
            continue
        distinctive = _EVENT_WORD.sub("", event).strip()
        if len(distinctive) < 2 or distinctive.casefold() in {
            "this",
            "our",
            "your",
            "a",
            "the",
        }:
            continue
        if re.search(
            r"(?<!\w)" + re.escape(_text(event).casefold()) + r"(?!\w)",
            title.casefold(),
        ):
            return event
    return None


def _personal_participation(body: str) -> bool:
    for match in _REGISTERED.finditer(body):
        clause = match[1]
        if _conditional_before(body, match.start()) or re.search(
            r"newsletter|mailing\s+list|updates|구독|뉴스레터", clause, re.IGNORECASE
        ):
            continue
        if re.search(
            r"\b(?:event|conference|workshop|hackathon|challenge|competition|lecture|seminar|webinar|mentoring)\b",
            clause,
            re.IGNORECASE,
        ):
            return True
    return _affirmed_match(
        re.compile(
            r"\byour\s+(?:speaker\s+)?(?:proposal|submission|registration)\s+(?:was|is|has\s+been)\s+(?:accepted|confirmed|received)\b"
            r"|(?:신청하신|등록하신|예약하신|지원하신|제출하신|참가\s*중인)"
            r"|(?:참가|참여|수강)?\s*(?:신청|등록|예약)(?:이|가|은|는)?\s*(?:확정|완료|승인)",
            re.IGNORECASE,
        ),
        body,
    )


def _personal_obligation(source: str) -> bool:
    return _personal_participation(source) or _affirmed_match(
        re.compile(
            r"\byour\s+(?:invoice|payment|subscription|account)\b.{0,60}\b(?:is\s+due|overdue|expires?|suspended|will\s+be\s+deleted)\b"
            r"|(?:회원님|고객님|귀하|본인|청구서).{0,60}(?:납부\s*기한|결제\s*기한|미납|만료|정지|삭제\s*예정)",
            re.IGNORECASE,
        ),
        source,
    )


@dataclass(frozen=True, slots=True)
class MailSourcePolicy:
    importance_choices: tuple[str, ...] = _ALL_IMPORTANCE
    prompt: dict[str, object] = field(default_factory=dict)
    _blocked_tags: frozenset[str] = frozenset()
    _forecast: tuple[Decimal, Decimal] | None = None
    _registered_deadline: bool = False
    _relative_days: bool = False
    _source_dates: frozenset[date] = frozenset()
    _source_month_days: frozenset[tuple[int, int]] = frozenset()
    _source_clocks: frozenset[tuple[int, int, int]] = frozenset()
    _source_time_zones: frozenset[str] = frozenset()
    _deletion_date: date | None = None
    _personal_loss: bool = False
    _balance_loss: bool = False
    _irreversible_loss: bool = False
    _third_party_app: bool = False
    _receipt_date: date | None = None
    _source_texts: tuple[str, ...] = field(default=(), repr=False)

    def tag_allowed(self, tag: str) -> bool:
        return _tag_key(tag) not in self._blocked_tags

    def relative_deadline_example(self) -> str | None:
        """Show faithful wording for an unambiguous, relative-only source reminder."""
        if (
            not self._registered_deadline
            or not self._relative_days
            or self._source_dates
            or self._source_month_days
            or self._source_clocks
            or self._source_time_zones
        ):
            return None
        facts = self.prompt.get("source_facts", {})
        days = (
            facts.get("relative_days_at_send_time") if isinstance(facts, dict) else None
        )
        if (
            not isinstance(days, list)
            or len(days) != 1
            or type(days[0]) is not int
            or not 0 <= days[0] <= 999
        ):
            return None
        phrase = next(
            (
                match.group()
                for source in self._source_texts
                for match in _RELATIVE_DAYS.finditer(source)
                if int(match.group(1)) == days[0]
            ),
            None,
        )
        if phrase is None:
            return None
        example = (
            "A submission reminder for an event you registered for: "
            f"'{phrase}' when the email was sent."
        )
        return None if self.summary_issues(example) else example

    def summary_issues(self, text: str) -> tuple[str, ...]:
        summary = _text(text)
        issues = []
        if self._forecast is not None:
            if not re.search(
                r"예상|예측|전망|\b(?:forecast\w*|expected|estimated)\b",
                summary,
                re.IGNORECASE,
            ):
                issues.append(
                    "Describe the AWS amount as forecasted or expected, not an already incurred charge."
                )
            if not set(self._forecast).issubset(_money_values(summary)):
                issues.append(
                    "Include both the source budget and forecast amounts with explicit USD or dollar currency markers."
                )
        if self._registered_deadline:
            if re.search(
                r"(?:등록|신청|참가).{0,12}(?:경우|다면|라면)"
                r"|\bif\s+you\s+(?:have\s+)?(?:registered|signed\s+up)\b",
                summary,
                re.IGNORECASE,
            ):
                issues.append(
                    "The source confirms the recipient registered; do not describe registration as an unknown condition."
                )
            if not re.search(
                r"제출|출품|마감|리마인더|\b(?:submission|submit|deadline|reminder)\b",
                summary,
                re.IGNORECASE,
            ):
                issues.append(
                    "Describe this personally applicable mail as a submission or deadline reminder in English."
                )
            if self._relative_days:
                # An exact quoted source span may contain a period before its
                # surrounding send-time attribution. Reuse discovery's strict
                # source/time exemption instead of splitting inside that quote.
                relative_prose = temporal_copy_prose(
                    summary, evidence_texts=self._source_texts
                )
                for sentence in re.split(r"[.!?。！？;；]+", relative_prose):
                    # A source-backed calendar date is not a count of days left.
                    relative_text = _MONTH_DAY.sub(" ", _FULL_DATE.sub(" ", sentence))
                    if _RELATIVE_SUMMARY.search(relative_text) and (
                        not _RECEIPT_QUALIFIER.search(sentence)
                        or re.search(
                            r"\b(?:now|currently|today)\b", sentence, re.IGNORECASE
                        )
                    ):
                        issues.append(
                            "Attribute relative days remaining to the email send or receipt time; do not present them as the current countdown."
                            " Copy a short verbatim source quote in its original language, followed in the same sentence by the literal attribution 'when the email was sent'."
                            " You may omit an optional relative countdown, but retain any stated absolute deadline and all mandatory source facts. Never invent a missing date, year or time zone."
                        )
                        break
            if _unproven_summary_date(
                summary, self._source_dates, self._source_month_days, self._receipt_date
            ):
                issues.append(
                    "Do not invent an absolute submission deadline that is absent from the source."
                )
            clocks, invalid_clock = _clock_values(summary)
            zones = _time_zones(summary)
            if invalid_clock or not clocks.issubset(self._source_clocks):
                issues.append(
                    "Do not add or change a submission time absent from the source."
                )
            if not zones.issubset(self._source_time_zones) or (
                clocks and self._source_time_zones and not zones
            ):
                issues.append(
                    "Preserve the explicit source time zone; do not infer or convert a deadline to another time zone."
                )
        if self._deletion_date is not None and self._deletion_date not in _full_dates(
            summary
        ):
            issues.append(
                "Include the explicit scheduled deletion date as ISO YYYY-MM-DD, preserving the source year."
            )
        loss_word = bool(
            re.search(
                r"삭제|소멸|사라|지워|상실|잃|\b(?:delet\w*|eras\w*|remov\w*|forfeit\w*|lost|loss)\b",
                summary,
                re.IGNORECASE,
            )
        )
        if self._personal_loss and (
            not re.search(_PERSONAL_DATA, summary, re.IGNORECASE) or not loss_word
        ):
            issues.append(
                "Preserve the source statement that personal data will be deleted or lost."
            )
        if self._balance_loss and (
            not re.search(_BALANCE, summary, re.IGNORECASE) or not loss_word
        ):
            issues.append(
                "Preserve the source statement that the account balance will be deleted or lost."
            )
        if self._irreversible_loss and not (
            _IRREVERSIBLE.search(summary)
            or re.search(r"영구|\bpermanent(?:ly)?\b", summary, re.IGNORECASE)
        ):
            issues.append(
                "Preserve that the stated deletion or loss cannot be recovered."
            )
        if self._third_party_app and _WRONG_THIRD_PARTY.search(summary):
            issues.append(
                "Third-party application means an external-party application, not the ordinal third application."
            )
        return tuple(dict.fromkeys(issues))


def source_policy(record: EvidenceRecord, *, description: str) -> MailSourcePolicy:
    title = _text(record.title)
    body = _text(record.untrusted_text or "")
    source = title + " " + body
    sender = _sender(record)
    receipt_date = _receipt_date_from_facts(record)
    templates = []
    facts: dict[str, object] = {}
    guidance = []
    blocked: set[str] = set()
    force_high = False
    outreach = False
    routine_authentication = False
    general_information = False
    forecast = None
    registered_deadline = False
    relative_days = False
    deletion_date = None
    personal_loss = balance_loss = irreversible_loss = False

    if (
        sender in {"google.com", "accounts.google.com"}
        and re.search(r"google\s*(?:account|계정)", title, re.IGNORECASE)
        and re.search(r"공유|\bshar(?:ed|ing)\b", title, re.IGNORECASE)
        and re.search(r"이름|\bname\b", body, re.IGNORECASE)
        and re.search(r"사진|\bphoto\b|\bpicture\b", body, re.IGNORECASE)
        and re.search(r"이메일|전자\s*메일|\be-?mail\b", body, re.IGNORECASE)
    ):
        templates.append("google_account_profile_sharing")
        blocked.update(_DATASET_TAGS)
        facts["shared_profile_fields"] = ["name", "photo", "email"]
        guidance.append(
            "Account profile information sharing is not a dataset update. Describe only access explicitly listed in the source."
        )

    if sender == "costalerts.amazonaws.com" and re.search(
        r"\bAWS\s+Budgets?\b", title, re.IGNORECASE
    ):
        budget_types = {
            value.casefold()
            for value in re.findall(
                r"Budget\s*Type\s*:\s*([A-Za-z]+)", body, re.IGNORECASE
            )
        }
        alert_types = {
            value.casefold()
            for value in re.findall(
                r"Alert\s*Type\s*:\s*([A-Za-z]+)", body, re.IGNORECASE
            )
        }
        budget = _amount_field(body, r"Budgeted\s*Amount")
        predicted = _amount_field(body, r"FORECASTED\s*Amount")
        if (
            budget_types == {"cost"}
            and alert_types == {"forecasted"}
            and budget is not None
            and predicted is not None
        ):
            templates.append("aws_forecast_budget")
            forecast = (budget, predicted)
            force_high = True
            blocked.update(_DATASET_TAGS | _SECURITY_TAGS)
            facts["budget_usd"] = _exact_decimal(budget)
            facts["forecast_usd"] = _exact_decimal(predicted)
            facts["cost_kind"] = "FORECASTED"
            guidance.append(
                "Preserve both currency-marked amounts and the forecast qualification; this is a budget notice, not a security or dataset event."
            )

    event = (
        _registered_event(title, body)
        if _FINAL_TITLE.search(title) or _body_submission_cutoff(body)
        else None
    )
    if event is not None:
        templates.append("registered_event_submission_reminder")
        registered_deadline = True
        relative_days = bool(_RELATIVE_DAYS.search(body))
        force_high = True
        blocked.update(_DATASET_TAGS | _SECURITY_TAGS)
        facts["registered_event"] = event
        facts["relative_days_at_send_time"] = sorted(
            {int(value) for value in _RELATIVE_DAYS.findall(body)}
        )
        if receipt_date is not None:
            facts["received_date_utc"] = receipt_date.isoformat()
            guidance.append(
                "The known UTC receipt date may identify mail timing; do not turn it into a submission deadline or guess a local-time date."
            )
        guidance.append(
            "The recipient explicitly registered for this named event; do not hedge this as 'if registered'. Preserve the submission reminder and its stated relative days remaining, attributed to the email's time rather than the current date."
        )

    deletion_dates = set()
    if _ACCOUNT_DELETION.search(title):
        for value in _DELETION_DATE.findall(body):
            try:
                deletion_dates.add(date.fromisoformat(value))
            except ValueError:
                continue
    if len(deletion_dates) == 1:
        deletion_date = next(iter(deletion_dates))
        templates.append("scheduled_account_deletion")
        force_high = True
        facts["scheduled_deletion_date"] = deletion_date.isoformat()
        if _IRREVERSIBLE.search(body):
            personal_loss = bool(
                re.search(
                    _PERSONAL_DATA + r".{0,80}" + _SOURCE_LOSS, body, re.IGNORECASE
                )
            )
            balance_loss = bool(
                re.search(_BALANCE + r".{0,80}" + _SOURCE_LOSS, body, re.IGNORECASE)
            )
            irreversible_loss = personal_loss or balance_loss
        facts["personal_data_loss"] = personal_loss
        facts["balance_loss"] = balance_loss
        facts["irreversible_loss"] = irreversible_loss
        guidance.append(
            "State the explicit scheduled deletion date and any source-supported irreversible loss; do not omit consequences."
        )

    account_event = _reported_account_event(title, body)
    auth_request = _authentication_request(title, body)
    if auth_request is not None:
        templates.append("transient_authentication_request")
        routine_authentication = True
        blocked.update(_SECURITY_TAGS | _DATASET_TAGS)
        facts["authentication_request_kind"] = auth_request
        guidance.append(
            "This source requests a one-time authentication step. Conditional advice for an unrequested code or reset does not report that account compromise occurred."
        )

    if (
        (
            _OUTREACH.search(title)
            or (_EVENT_OUTREACH.search(title) and _OUTREACH_BODY.search(body))
        )
        and not _SECURITY_WARNING.search(source)
        and not account_event
        and not _personal_obligation(source)
    ):
        templates.append("generic_participation_outreach")
        outreach = True
        blocked.update(_SECURITY_TAGS)
        if not _DATASET_SOURCE.search(source):
            blocked.update(_DATASET_TAGS)
        guidance.append(
            "A generic recruitment or call for participation is not evidence of this recipient's existing obligation or an account-security alert."
        )

    if (
        all(
            pattern.search(body)
            for pattern in (
                _EDUCATIONAL_PROGRAM,
                _EDUCATIONAL_SPEAKERS,
                _EDUCATIONAL_PROMOTION,
                _EDUCATIONAL_EVENT_CONTEXT,
            )
        )
        and not _SECURITY_WARNING.search(title)
        and not account_event
        and not _personal_obligation(source)
        and not _affirmed_match(_EDUCATIONAL_RECIPIENT_DUTY, source)
    ):
        templates.append("educational_event_promotion")
        blocked.update(_SECURITY_TAGS)
        if not _DATASET_SOURCE.search(source):
            blocked.update(_DATASET_TAGS)
            general_information = True
        guidance.append(
            "A program promoting educational sessions and speakers is not a recipient-specific account-security incident. "
            "Security case studies describe session topics; do not infer an account incident or a personal obligation from the program."
        )

    for name, title_pattern, body_pattern, explanation in (
        (
            "general_information_digest",
            _INFORMATION_TITLE,
            _INFORMATION_BODY,
            "A general newsletter or research digest is not a recipient-specific account-security incident. General model or paper news is not a dataset release without explicit dataset content.",
        ),
        (
            "commercial_promotion",
            _MARKETING_TITLE,
            _MARKETING_BODY,
            "A commercial offer about a security product does not itself report an incident affecting the recipient's account.",
        ),
        (
            "general_terms_update",
            _TERMS_TITLE,
            _TERMS_BODY,
            "A general service-terms change is a policy notice, not an account-security incident.",
        ),
    ):
        if (
            title_pattern.search(title)
            and body_pattern.search(body)
            and not account_event
        ):
            templates.append(name)
            general_information = general_information or not _personal_obligation(
                source
            )
            blocked.update(_SECURITY_TAGS)
            if not _DATASET_SOURCE.search(source):
                blocked.update(_DATASET_TAGS)
            guidance.append(explanation)

    third_party_app = bool(
        _THIRD_PARTY.search(source) and not _SOURCE_ORDINAL.search(source)
    )
    if third_party_app:
        facts["third_party_application_is_external"] = True
        guidance.append(
            "Translate third-party application as an external-party app, not the ordinal third app."
        )
    custom = bool(description.strip())
    choices = (
        _ALL_IMPORTANCE
        if custom
        else ("HIGH",)
        if force_high
        else ("LOW",)
        if routine_authentication
        else ("NORMAL", "LOW")
        if outreach or general_information
        else _ALL_IMPORTANCE
    )
    if custom:
        blocked.clear()
    prompt = {}
    if templates or third_party_app:
        prompt = {
            "recognized_templates": templates,
            "source_facts": facts,
            "guidance": guidance,
            "custom_description_controls_relevance": custom,
        }
    return MailSourcePolicy(
        importance_choices=choices,
        prompt=prompt,
        _blocked_tags=frozenset(blocked),
        _forecast=forecast,
        _registered_deadline=registered_deadline,
        _relative_days=relative_days,
        _source_dates=frozenset(_full_dates(source)),
        _source_month_days=frozenset(_month_days(source)),
        _source_clocks=frozenset(_clock_values(source)[0]),
        _source_time_zones=_time_zones(source),
        _deletion_date=deletion_date,
        _personal_loss=personal_loss,
        _balance_loss=balance_loss,
        _irreversible_loss=irreversible_loss,
        _third_party_app=third_party_app,
        _receipt_date=receipt_date,
        _source_texts=(title, body),
    )
