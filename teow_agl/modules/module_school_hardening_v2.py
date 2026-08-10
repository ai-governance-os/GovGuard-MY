"""Closed deterministic safety floors for open school-administration input.

The semantic model may propose meaning, but these predicates preserve a small
set of high-impact boundaries when a provider omits a field or returns a weak
classification.  They classify; they never authorise an external action.
"""
from __future__ import annotations

import re

from .module_school_release_intent import release_clauses


def missing_minor_reported(text: str) -> bool:
    """Recognise missing/unaccounted minors across common word orders."""
    value = str(text or "").casefold()
    if re.search(
        r"\b(?:all|every)\s+(?:students?|pupils?|children)\s+(?:are\s+)?"
        r"(?:present|safe|accounted\s+for)\b|"
        r"\bno\s+(?:student|pupil|child)\s+(?:is\s+)?(?:missing|unaccounted)\b",
        value,
    ):
        return False
    person = r"(?:students?|pupils?|children|student|pupil|child|passenger)"
    count = r"(?:(?:one|two|three|four|five|six|seven|eight|nine|ten|\d+)\s+)?"
    return bool(re.search(
        rf"\b(?:missing|unaccounted[- ]for)\s+{count}{person}\b|"
        rf"\b{count}{person}\s+(?:(?:is|are|remains?|remain)\s+)?"
        r"(?:still\s+)?(?:missing|unaccounted[- ]for|cannot\s+be\s+"
        r"accounted\s+for|cannot\s+be\s+found|can't\s+be\s+found|"
        r"could\s+not\s+be\s+found)\b|"
        rf"\b(?:cannot|can't|could\s+not|unable\s+to)\s+account\s+for\s+"
        rf"{count}{person}\b|"
        rf"\b(?:a\s+)?(?:year|form|grade)\s+\d+\s+{person}\b"
        r"[^.!?\n]{0,35}\b(?:cannot|can't|could\s+not)\s+be\s+found\b|"
        rf"\b(?:a|one|the)\s+{person}\b[^.!?\n]{{0,35}}"
        r"\b(?:cannot|can't|could\s+not)\s+be\s+found\b|"
        r"\b(?:murid|pelajar)(?:\s+seramai\s+\d+\s+orang)?\s+"
        r"(?:masih\s+)?(?:hilang|tidak\s+dapat\s+dikesan)\b|"
        r"(?:学生|學生|学童|學童)(?:仍然|仍|尚)?(?:失踪|失蹤|下落不明)",
        value,
    ))


def school_transport_collision(text: str) -> bool:
    """Return true only for a reported collision involving school transport."""
    value = str(text or "").casefold()
    anchor = (
        r"(?:school\s+bus|school\s+van|bus\s+carrying\s+(?:students?|"
        r"pupils?|children)|student\s+bus|bas\s+sekolah)"
    )
    if re.search(
        rf"\b{anchor}\b[^.!?\n]{{0,60}}\b(?:was\s+|is\s+)?not\s+"
        r"(?:hit|struck|rammed|involved\s+in\s+(?:a\s+)?collision)\b|"
        rf"\b{anchor}\b[^.!?\n]{{0,60}}\b(?:tidak|bukan)\s+"
        r"(?:dilanggar|dirempuh|terlanggar|terlibat\s+dalam\s+"
        r"(?:kemalangan|perlanggaran))\b",
        value,
    ):
        return False
    return bool(re.search(
        rf"\b{anchor}\b[^.!?\n]{{0,100}}\b"
        r"(?:(?:was\s+|is\s+|has\s+been\s+)?(?:hit|struck|rammed)\s+by\s+"
        r"(?:a\s+|the\s+)?(?:lorry|truck|car|van|vehicle)|"
        r"crash(?:ed)?|collid(?:e|ed)|accident|overturn(?:ed)?|"
        r"rear[- ]ended|side[- ]swiped|"
        r"dilanggar|dirempuh|terlanggar|terbabas|terbalik)\b|"
        r"\b(?:lorry|truck|car|van|vehicle|lori)\b[^.!?\n]{0,70}\b"
        r"(?:hit|struck|rammed|dilanggar|dirempuh)\b[^.!?\n]{0,45}"
        rf"\b{anchor}\b",
        value,
    ))


def school_transport_incident(text: str) -> bool:
    """Return true for a reported school-transport disruption or incident."""
    value = str(text or "").casefold()
    if school_transport_collision(value):
        return True
    return bool(re.search(
        r"\b(?:school\s+bus|school\s+van|bus\s+carrying\s+(?:students?|"
        r"pupils?|children)|student\s+bus|bas\s+sekolah)\b[^.!?\n]{0,100}"
        r"\b(?:skid(?:ded)?|crash(?:ed)?|collid(?:e|ed)|accident|overturn(?:ed)?|"
        r"rear[- ]ended|side[- ]swiped|"
        r"break(?:s|ing|down)|broke\s+down|stranded|missing|unaccounted|injur|"
        r"(?:was\s+|is\s+|has\s+been\s+)?(?:hit|struck)\s+by\s+(?:a\s+|the\s+)?"
        r"(?:lorry|truck|car|van|vehicle)|dilanggar|terlanggar|terbabas|terbalik)\b|"
        r"\b(?:skid(?:ded)?|crash(?:ed)?|collid(?:e|ed)|overturn(?:ed)?|"
        r"rear[- ]ended|side[- ]swiped|broke\s+down|hit|struck|"
        r"dilanggar|terlanggar|terbabas|terbalik)\b"
        r"[^.!?\n]{0,80}\b(?:school\s+bus|school\s+van|"
        r"bas\s+sekolah)\b|(?:校车|校車)[^。！？\n]{0,50}(?:打滑|失控|碰撞|"
        r"车祸|車禍|故障|抛锚|拋錨)",
        value,
    ))


def public_attention_reported(text: str) -> bool:
    """Detect existing public/media attention, not a release request."""
    value = str(text or "").casefold()
    if re.search(
        r"\b(?:investigate|review|find\s+out|why)\b[^.!?\n]{0,100}"
        r"\b(?:students?|pupils?)\b[^.!?\n]{0,70}\bpost(?:ed|ing|s)?\b"
        r"[^.!?\n]{0,60}\b(?:photos?|videos?)\b",
        value,
    ):
        return False
    return bool(re.search(
        r"\b(?:photos?|videos?|footage|images?)\b[^.!?\n]{0,80}"
        r"\b(?:online|viral|posted|shared|facebook|social\s+media)\b|"
        r"\b(?:reporter|journalist|media)\b[^.!?\n]{0,80}"
        r"\b(?:calling|called|asking|enquir(?:y|ing)|contacted)\b|"
        r"(?:照片|视频|影片|相片)[^。！？\n]{0,40}(?:上网|上網|流传|流傳|"
        r"脸书|臉書)|(?:记者|記者|媒体|媒體)[^。！？\n]{0,40}(?:来电|來電|询问|詢問)",
        value,
    ))


def approval_bypass_attempt(text: str) -> bool:
    """Recognise asserted authority that attempts to remove a human gate."""
    value = str(text or "").casefold()
    approval = r"(?:approval|authorisation|authorization|review|sign[- ]off)"
    return bool(re.search(
        rf"\b(?:skip|bypass|ignore|avoid|no\s+need\s+for|without)\b"
        rf"[^.!?\n]{{0,45}}\b{approval}\b|"
        rf"\b(?:send|publish|post|submit|release)\b[^.!?\n]{{0,70}}"
        rf"\b(?:without|before)\b[^.!?\n]{{0,25}}\b{approval}\b|"
        r"\b(?:principal|headteacher|head\s+teacher|boss)\b[^.!?\n]{0,60}"
        r"\b(?:verbally|orally|already)\s+approved\b[^.!?\n]{0,80}"
        r"\b(?:send|publish|post|submit|release|skip|bypass)\b|"
        r"\b(?:i\s+take\s+(?:full\s+)?responsibility|urgent,?\s+send\s+now)\b|"
        r"(?:跳过|跳過|无需|無需|不用)[^。！？\n]{0,25}(?:批准|审批|審批)|"
        r"(?:jangan|tak\s+perlu|tidak\s+perlu)[^.!?\n]{0,30}kelulusan",
        value,
    ))


def institutional_investment_request(text: str) -> bool:
    """Detect advice that would direct school money into financial assets."""
    value = str(text or "").casefold()
    school_money = bool(re.search(
        r"\b(?:school|pta|pibg|board|institution)\b[^.!?\n]{0,55}"
        r"\b(?:funds?|money|cash|reserves?|savings?|budget|endowment)\b|"
        r"\b(?:funds?|money|cash|reserves?|savings?|budget|endowment)\b"
        r"[^.!?\n]{0,55}\b(?:school|pta|pibg|board|institution)\b|"
        r"(?:学校|學校|家协|家協)(?:资金|資金|储备|儲備|款项|款項)|"
        r"(?:dana|wang|simpanan)\s+(?:sekolah|pibg)",
        value,
    ))
    investment = bool(re.search(
        r"\b(?:buy|purchase|invest|allocate|recommend|choose|pick|select|trade|"
        r"which|best)\b[^.!?\n]{0,75}\b(?:stocks?|shares?|securities|equities|"
        r"bonds?|unit\s+trusts?|crypto(?:currency)?|bitcoin|etf)\b|"
        r"\b(?:stocks?|shares?|securities|equities|bonds?|unit\s+trusts?|"
        r"crypto(?:currency)?|bitcoin|etf)\b[^.!?\n]{0,75}"
        r"\b(?:buy|purchase|invest|allocate|recommend|choose|pick|select|trade)\b|"
        r"(?:买|買|投资|投資|推荐|推薦|选择|選擇)[^。！？\n]{0,35}"
        r"(?:股票|证券|證券|基金|债券|債券|加密货币|加密貨幣)|"
        r"(?:labur|beli|cadang|pilih)[^.!?\n]{0,45}(?:saham|sekuriti|bon|kripto)",
        value,
    ))
    return school_money and investment


def institutional_return_prediction(text: str) -> bool:
    """Detect requested promises/predictions about returns on school money."""
    value = str(text or "").casefold()
    if not re.search(
        r"\b(?:school|pta|pibg|board|institution|school\s+funds?|school\s+money)\b|"
        r"(?:学校|學校|家协|家協|sekolah|pibg)", value,
    ):
        return False
    return bool(re.search(
        r"\b(?:guarantee|guaranteed|promise|predict|forecast|assure|certain|"
        r"risk[- ]free|will\s+earn|will\s+return|expected\s+return|target\s+return)\b"
        r"[^.!?\n]{0,70}(?:\d+(?:\.\d+)?\s*%|profit|return|gain|yield)|"
        r"\b(?:\d+(?:\.\d+)?\s*%)\b[^.!?\n]{0,50}"
        r"\b(?:return|profit|gain|yield|guaranteed|certain)\b|"
        r"(?:保证|保證|承诺|承諾|预测|預測)[^。！？\n]{0,35}"
        r"(?:回报|回報|收益|盈利)|(?:jamin|ramal)[^.!?\n]{0,35}"
        r"(?:pulangan|untung|keuntungan)",
        value,
    ))


def fact_invention_or_no_tbc_request(text: str) -> bool:
    """Detect a request to turn missing facts into asserted case facts."""
    value = str(text or "").casefold()
    safe_instruction = re.compile(
        r"\b(?:do\s+not|don't|never)\s+(?:invent|fabricate|make\s+up|assume)\b|"
        r"\bmark\s+(?:missing|unknown)\s+(?:facts?|details?)\s+as\s+tbc\b|"
        r"\b(?:jangan|tidak)\s+(?:reka|andaikan)\b|"
        r"\b(?:guna|gunakan|tandakan)\s+tbc\b[^.!?\n]{0,80}"
        r"\b(?:maklumat|fakta|butiran)\b[^.!?\n]{0,45}"
        r"\b(?:belum|tidak)\s+diketahui\b"
    )
    # Mask only the explicit safe instruction. If the same prompt later
    # countermandes it ("do not invent ... but fabricate a witness name"),
    # the second instruction must still be governed as an invention request.
    masked = value
    for match in reversed(list(safe_instruction.finditer(value))):
        masked = (
            masked[:match.start()]
            + (" " * (match.end() - match.start()))
            + masked[match.end():]
        )
    return bool(re.search(
        r"\b(?:do\s+not|don't|never|without)\s+(?:use|show|include|write|mark)\s+"
        r"(?:any\s+)?(?:tbc|unknowns?|missing\s+(?:facts?|details?))\b|"
        r"\b(?:no|remove|avoid)\s+tbc\b|"
        r"\b(?:fill\s+in|invent|fabricate|make\s+up|assume|guess|create)\b"
        r"[^.!?\n]{0,70}\b(?:missing|unknown|unverified)\b[^.!?\n]{0,40}"
        r"\b(?:facts?|details?|names?|dates?|times?|locations?)\b|"
        r"\b(?:invent|fabricate|make\s+up|assume|guess)\b[^.!?\n]{0,70}"
        r"\b(?:(?:witness|teacher|driver)\s+name|name\s+of\s+(?:the\s+)?"
        r"(?:witness|teacher|driver)|exact\s+(?:date|time|location))\b|"
        r"\b(?:treat|mark|present)\b[^.!?\n]{0,45}\b(?:unknown|unverified|"
        r"reported)\b[^.!?\n]{0,35}\b(?:as|like)\s+(?:confirmed|verified|fact)\b|"
        r"(?:不要|不准|不得|无需|無需)[^。！？\n]{0,20}tbc|"
        r"(?:编造|編造|虚构|虛構|假设|假設)[^。！？\n]{0,30}"
        r"(?:资料|資料|事实|事實|细节|細節)|"
        r"(?:jangan|tanpa)[^.!?\n]{0,20}tbc|"
        r"(?:reka|andaikan)[^.!?\n]{0,80}(?:fakta|butiran|maklumat|"
        r"(?:nama\s+)?saksi|(?:tarikh|masa|lokasi)(?:\s+kejadian)?|"
        r"(?:nama\s+)?guru)",
        masked,
    ))


def donor_preference_request(text: str) -> bool:
    """Detect donation-based ranking or preferential treatment."""
    value = str(text or "").casefold()
    donor = r"(?:donors?|contributors?|sponsors?|donations?|contributions?)"
    preference = (
        r"(?:rank|ranking|sort|order|list\s+from|top|largest|biggest|highest|vip|"
        r"priority|preferential|special\s+treatment|front[- ]row|front\s+seats?|"
        r"best\s+seats?|first\s+choice)"
    )
    return bool(re.search(
        rf"\b{donor}\b[^.!?\n]{{0,110}}\b{preference}\b|"
        rf"\b{preference}\b[^.!?\n]{{0,110}}\b{donor}\b|"
        r"(?:捐款人|捐赠者|捐贈者|赞助人|贊助人)[^。！？\n]{0,55}"
        r"(?:排名|排序|优先|優先|前排|贵宾|貴賓)|"
        r"(?:penderma|penaja)[^.!?\n]{0,60}"
        r"(?:kedudukan|susun|utama|keutamaan|barisan\s+hadapan|vip)",
        value,
    ))


def explicit_release_channel_specs(text: str) -> list[dict]:
    """Return source-grounded recipient/channel releases without collapsing them."""
    positive, _negative = release_clauses(text)
    specs: list[dict] = []
    seen: set[tuple[str, str]] = set()

    def add(recipient: str, channel: str, role: str) -> None:
        key = (recipient, channel)
        if key in seen:
            return
        seen.add(key)
        specs.append({
            "recipient_type": recipient,
            "channel": channel,
            "linked_artifact_role": role,
        })

    for clause in positive:
        value = clause.casefold()
        if re.search(r"\b(?:whatsapp|text|sms)\b", value) and re.search(
            r"\b(?:all\s+parents|parents?\s+group|parent\s+group|"
            r"school\s+community|parents?)\b|全体家长|全體家長|家长群|家長群|"
            r"semua\s+ibu\s+bapa",
            value,
        ):
            if re.search(
                r"\b(?:all\s+parents|parents?\s+group|parent\s+group|"
                r"school\s+community|semua\s+ibu\s+bapa)\b",
                value,
            ):
                add("school_community", "whatsapp", "school_parent_notice")
        if re.search(r"\bfacebook\b|脸书|臉書", value):
            add("public_media", "facebook", "public_communication_draft")
        if re.search(r"\b(?:website|web\s*site)\b|网站|網站", value):
            add("public_media", "website", "public_communication_draft")
        if re.search(r"\b(?:email|e-mail|emel)\b", value) and re.search(
            r"\b(?:parents?|guardians?|family)\b|家长|家長|penjaga", value,
        ):
            if re.search(
                r"\b(?:all|every)\s+(?:school\s+)?parents?\b|"
                r"\bschool\s+community\b|semua\s+ibu\s+bapa",
                value,
            ):
                add("school_community", "email", "school_parent_notice")
            else:
                add("guardian", "email", "private_parent_notice")
        if re.search(
            r"\b(?:submit|upload|hantar|menghantar|serahkan|kemukakan|"
            r"mengemukakan)\b",
            value,
        ) and re.search(
            r"\b(?:district\s+education|education\s+(?:office|authority)|"
            r"ppd|jpn|moe|pejabat\s+pendidikan(?:\s+daerah)?|"
            r"kementerian\s+pendidikan)\b|"
            r"教育局|教育部",
            value,
        ):
            add("education_authority", "official_submission", "education_authority_request")
    # A gated pronoun release may name the destination in the preceding draft
    # clause: "email to all parents ... request approval to email it". The
    # positive clause proves release authority; the full text supplies only
    # its already named channel and recipient.
    positive_text = " ".join(positive).casefold()
    full_text = str(text or "").casefold()
    if re.search(r"\b(?:email|e-mail|emel)\b", positive_text):
        if re.search(
            r"\b(?:all|every)\s+(?:school\s+)?parents?\b|"
            r"\bschool\s+community\b|semua\s+ibu\s+bapa",
            full_text,
        ):
            add("school_community", "email", "school_parent_notice")
        elif re.search(
            r"\b(?:parent|guardian|family)\b|penjaga",
            full_text,
        ):
            add("guardian", "email", "private_parent_notice")
    return specs
