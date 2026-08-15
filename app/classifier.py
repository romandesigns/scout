from __future__ import annotations

import html
import re
from bs4 import BeautifulSoup

from .config import settings


POSITIVE_RULES = [
    ("M&A / strategic", 5, [
        r"definitive agreement.{0,120}(?:acquire|merger)", r"merger agreement", r"to acquire", r"acquisition of",
        r"acquired by", r"tender offer",
    ]),
    ("FDA / regulatory", 5, [
        r"fda approval", r"fda approved", r"fda cleared", r"510\(k\).{0,60}clear", r"breakthrough device",
        r"fast track designation", r"priority review", r"clinical hold lifted", r"marketing authorization",
        r"regulatory approval", r"positive topline", r"met (?:its )?primary endpoint",
        r"statistically significant.{0,120}(?:primary endpoint|overall survival|progression-free survival)",
    ]),
    ("Major contract / order", 5, [
        r"awarded (?:a )?contract", r"contract award", r"purchase order", r"multi-year contract",
        r"master services agreement", r"defense contract", r"government contract",
        r"minimum.{0,80}(?:spend|commitment).{0,80}\$",
    ]),
    ("Partnership / licensing", 3, [
        r"strategic partnership", r"strategic collaboration", r"license agreement", r"licensing agreement",
        r"commercialization agreement", r"distribution agreement",
    ]),
    ("Earnings / guidance", 4, [
        r"raises? (?:full[- ]year )?guidance", r"increases? (?:full[- ]year )?guidance", r"raises? outlook",
        r"record (?:quarterly )?revenue", r"record sales",
        r"revenue.{0,80}(?:increased|grew|growth).{0,50}(?:[2-9]\d|\d{3,})%",
        r"net income.{0,80}(?:increased|grew|rose).{0,50}(?:[2-9]\d|\d{3,})%",
    ]),
    ("Dilution relief", 5, [
        r"terminat(?:e|ed|ion).{0,180}(?:securities purchase agreement|equity line|at-the-market|atm|offering|share purchase)",
        r"(?:securities purchase agreement|equity line|at-the-market|atm).{0,180}terminat(?:e|ed|ion)",
        r"withdraw(?:s|n|al).{0,120}(?:registration statement|offering)",
    ]),
    ("Balance-sheet improvement", 4, [
        r"debt extinguish", r"debt repayment", r"repay(?:s|ment).{0,80}debt", r"debt reduction",
        r"eliminat(?:es|ed).{0,80}debt",
    ]),
    ("Favorable litigation", 4, [
        r"lawsuit dismissed", r"case dismissed", r"favorable ruling", r"settlement.{0,120}(?:in favor|receiv|payment)",
    ]),
    ("Analyst upgrade", 3, [
        r"upgraded to (?:buy|outperform|overweight|strong buy)", r"price target (?:raised|increased)",
    ]),
]

RISK_RULES = [
    ("offering/dilution", 5, [
        r"registered direct offering", r"public offering", r"private placement", r"at-the-market offering",
        r"equity line", r"warrant inducement", r"convertible note", r"securities purchase agreement",
        r"prospectus supplement", r"424b5", r"s-1 registration",
    ]),
    ("reverse split/listing", 5, [
        r"reverse stock split", r"reverse share split", r"delisting notice", r"minimum bid price deficiency", r"nasdaq deficiency",
    ]),
    ("financial stress", 5, [r"bankruptcy", r"chapter 11", r"going concern", r"substantial doubt"]),
    ("clinical/regulatory negative", 5, [
        r"clinical hold", r"complete response letter", r"failed to meet.{0,100}primary endpoint",
        r"did not meet.{0,100}primary endpoint", r"fda reject", r"fda declined",
    ]),
]

NOISE_PATTERNS = [
    r"conference participation", r"investor conference", r"fireside chat", r"annual meeting",
    r"appoints? .*director", r"appoints? .*officer", r"presentation at", r"webinar",
]


def clean_text(value: str) -> str:
    value = html.unescape(value or "")
    value = BeautifulSoup(value, "html.parser").get_text(" ", strip=True)
    return re.sub(r"\s+", " ", value).strip()


def classify_bullish(text: str) -> tuple[int, list[str], int, list[str], bool]:
    t = clean_text(text).lower()
    positives: list[str] = []
    risks: list[str] = []
    positive_score = 0
    risk_score = 0
    for category, weight, patterns in POSITIVE_RULES:
        if any(re.search(p, t, flags=re.I | re.S) for p in patterns):
            positives.append(category)
            positive_score = max(positive_score, weight)
    for category, weight, patterns in RISK_RULES:
        if any(re.search(p, t, flags=re.I | re.S) for p in patterns):
            risks.append(category)
            risk_score = max(risk_score, weight)
    if any(re.search(p, t, flags=re.I | re.S) for p in NOISE_PATTERNS):
        positive_score = max(0, positive_score - 2)
    if "Dilution relief" in positives:
        risks = [x for x in risks if x != "offering/dilution"]
        if not risks:
            risk_score = 0
    bullish = positive_score >= settings.min_bullish_score and (risk_score == 0 or positive_score >= risk_score + 2)
    return positive_score, positives, risk_score, risks, bullish
