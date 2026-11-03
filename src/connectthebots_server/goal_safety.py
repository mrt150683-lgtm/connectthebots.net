"""Closed deterministic Common goals screen, versioned for deliberate review.

These rules flag wording; they do not establish safety or medical truth. They
never fetch URLs, infer diagnoses, or run a model. The short drug/condition
lists are lexical hold triggers, not claims about what treats a condition.

Initial M4 rationale: preserve the M1 prohibited-project list, add the source
plan's fixed contact/money/recruitment/personal-health refusals, and hold its
treatment-like language only in sensitive goals. Refusals also apply to quoted
sources; only contact detection is exempt inside sources/artifact fields.
NFKC/casefold is used solely for detection; approved/stored content is unchanged.
Changing a list or pattern requires a rationale, version and manifest test.
"""
from hashlib import sha256
import json
import re
import unicodedata

from .agent_common import AgentError, clean_text
from .agent_texts import GOAL_SAFETY_HOLD, GOAL_SAFETY_REFUSAL
from .topic_boards import CONTACT


REFUSED_PATTERNS = (
    r"\bweapons?\b", r"\bpathogens?\b", r"\btoxins?\b", r"\bgain[ -]of[ -]function\b",
    r"\bexplosives?\b", r"\bsurveillance\b", r"\bidentify(?:ing)? people\b",
    r"\bcyber[ -]attacks?\b", r"\battacks? on (?:computer )?systems\b",
    r"\belection persuasion\b",
)
_REFUSED = tuple(re.compile(pattern, re.IGNORECASE) for pattern in REFUSED_PATTERNS)

RULESET_VERSION = "goal-safety-v1"
PAYMENT_PATTERNS = (
    r"\b(?:donate|paypal|gofundme|patreon)\b",
    r"\b(?:make|send|give|request|solicit|collect)\s+(?:(?:a|your|some)\s+)?donations?\b",
    r"\b(?:send|transfer)\s+(?:money|funds|cash|crypto|bitcoin|ethereum)\b",
    r"\b(?:send|pay|give)\s+(?:me|us)\s+(?:money|funds|cash)\b",
    r"\b[a-z]{2}\d{2}(?: ?[a-z0-9]){11,30}\b",
    r"\b[13][a-z1-9]{25,34}\b",
    r"\bbc1[ac-hj-np-z02-9]{11,87}\b",
    r"\b0x[0-9a-f]{40}\b",
)
RECRUITMENT_PATTERNS = (
    r"\b(?:recruit(?:ing)?|looking for|seeking)\s+(?:patients?|trial participants)\b",
    r"\bvolunteers?\s+to\s+(?:try|take|receive)\b",
    r"\b(?:recruit(?:ing)?|looking for|seeking)\s+(?:people|participants|volunteers)\s+for\s+(?:(?:a|our|the)\s+)?(?:(?:clinical|medical|treatment)\s+)?trials?\b",
    r"\b(?:join|participate in|sign up for)\s+(?:(?:a|our|the)\s+)?(?:clinical|medical|treatment)\s+trials?\b",
)
PERSONAL_HEALTH_PATTERNS = (
    r"\bmy\s+(?:diagnosis|medical records|blood\s*work|blood test results)\b",
    r"\bmy\s+(?:dog|cat|pet)(?:'s|s'|s)?\s+(?:blood\s*work|diagnosis|test results)\b",
)
DRUG_SUPPLEMENT_WORDS = (
    "aspirin", "ibuprofen", "paracetamol", "amoxicillin", "prednisone", "prednisolone", "metformin", "insulin",
    "ivermectin", "rapamycin", "melatonin", "magnesium", "vitamin d", "turmeric", "cbd", "resveratrol",
)
CONDITION_WORDS = (
    "cancer", "diabetes", "arthritis", "anxiety", "depression", "dementia", "infection", "pain", "obesity", "insomnia",
    "kidney disease", "heart disease", "ageing", "aging", "longevity",
)
NAMED_HEALTH_TEMPLATES = (
    r"\b[a-z][a-z'-]{1,29}(?:\s+[a-z][a-z'-]{1,29}){0,2}\s+(?:has|was diagnosed with|is living with|suffers from|is being treated for)\s+(?:(?:a|an)\s+)?(?:{conditions})\b",
    r"\b(?:patient|person|individual)\s+(?:named\s+)?[a-z][a-z'-]{1,29}(?:\s+[a-z][a-z'-]{1,29}){0,2}\s+with\s+(?:{conditions})\b",
)
CLAIM_WORDS = ("cure", "cures", "cured", "guaranteed", "proven to", "miracle", "reverses")
DOSE_UNITS = ("mg", "mcg", "µg", "ml", "iu", "g/kg", "mg/kg")
DOSE_TEMPLATE = r"(?<![\w.])(?:\d+(?:\.\d+)?|\.\d+)\s*(?:{units})\b"
INSTRUCTION_VERBS = ("take", "stop", "start", "give")
INSTRUCTION_TEMPLATES = (r"\byou should\s+(?:{verbs})\b", r"\binstead of\s+(?:a|your)\s+(?:doctor|vet)\b")
DRUG_CONDITION_TEMPLATE = r"\b(?:{drugs})\s+for\s+(?:{conditions})\b"
HOLD_CODES = ("sensitive_claim", "sensitive_dose", "sensitive_instruction", "sensitive_drug_condition")


def _normal(text):
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", text).casefold().replace("’", "'").replace("‘", "'"))


def _alternatives(words):
    return "|".join(re.escape(_normal(word)) for word in words)


_PAYMENT = tuple(re.compile(pattern) for pattern in PAYMENT_PATTERNS)
_RECRUITMENT = tuple(re.compile(pattern) for pattern in RECRUITMENT_PATTERNS)
_PERSONAL = tuple(re.compile(pattern) for pattern in PERSONAL_HEALTH_PATTERNS) + tuple(
    re.compile(template.replace("{conditions}", _alternatives(CONDITION_WORDS))) for template in NAMED_HEALTH_TEMPLATES)
_HOLDS = (
    (HOLD_CODES[0], (re.compile(r"\b(?:" + _alternatives(CLAIM_WORDS) + r")\b"),)),
    (HOLD_CODES[1], (re.compile(DOSE_TEMPLATE.replace("{units}", _alternatives(sorted(DOSE_UNITS, key=len, reverse=True)))),)),
    (HOLD_CODES[2], tuple(re.compile(template.replace("{verbs}", _alternatives(INSTRUCTION_VERBS))) for template in INSTRUCTION_TEMPLATES)),
    (HOLD_CODES[3], (re.compile(DRUG_CONDITION_TEMPLATE.replace("{drugs}", _alternatives(DRUG_SUPPLEMENT_WORDS)).replace("{conditions}", _alternatives(CONDITION_WORDS))),)),
)

RULE_MANIFEST = {
    "version": RULESET_VERSION, "prohibited_project": REFUSED_PATTERNS, "contact": CONTACT.pattern,
    "contact_exempt_fields": ("sources", "artifact"), "payment": PAYMENT_PATTERNS, "recruitment": RECRUITMENT_PATTERNS,
    "personal_health": PERSONAL_HEALTH_PATTERNS, "named_health_templates": NAMED_HEALTH_TEMPLATES,
    "drugs_supplements": DRUG_SUPPLEMENT_WORDS, "conditions": CONDITION_WORDS, "claims": CLAIM_WORDS,
    "dose_units": DOSE_UNITS, "dose_template": DOSE_TEMPLATE, "instruction_verbs": INSTRUCTION_VERBS,
    "instruction_templates": INSTRUCTION_TEMPLATES, "drug_condition_template": DRUG_CONDITION_TEMPLATE,
    "hold_codes": HOLD_CODES,
}
RULESET_SHA256 = sha256(json.dumps(RULE_MANIFEST, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")).hexdigest()


def check_proposal(text):
    """Legacy prohibited-project gate, including source title/quote callers.

    Full sharing paths must call screen_content on their validated structured
    content, since only that context identifies source/artifact contact fields.
    """
    if any(pattern.search(_normal(text)) for pattern in _REFUSED):
        raise AgentError(422, "This goal falls outside the permitted research and building categories.")


def screen_content(value, tier="standard"):
    """Refuse prohibited sharing or report ordered sensitive-tier hold reasons.

    JSON dictionaries/lists/text and JSON scalar metadata are accepted. The
    iterative walk preserves field context, handles deep valid structures and
    rejects cycles/non-JSON values. Strings still pass existing clean_text
    validation; the screen does not invent per-field length limits.
    """
    if tier not in ("standard", "sensitive"):
        raise AgentError(422, "Choose a supported goal tier for screening.")
    reasons, active = set(), set()
    stack = [(value, False, False)]
    while stack:
        current, contact_exempt, leaving = stack.pop()
        if isinstance(current, (dict, list)):
            if leaving:
                active.remove(id(current))
                continue
            if id(current) in active:
                raise AgentError(422, GOAL_SAFETY_REFUSAL)
            active.add(id(current))
            stack.append((current, contact_exempt, True))
            if isinstance(current, dict):
                if any(not isinstance(key, str) for key in current):
                    raise AgentError(422, GOAL_SAFETY_REFUSAL)
                stack.extend((item, contact_exempt or key in ("sources", "artifact"), False) for key, item in reversed(tuple(current.items())))
            else:
                stack.extend((item, contact_exempt, False) for item in reversed(current))
            continue
        if current is None or type(current) in (bool, int):
            continue
        if not isinstance(current, str):
            raise AgentError(422, GOAL_SAFETY_REFUSAL)
        clean_text(current, "goal content", max(1, len(current)), minimum=0, multiline=True)
        text = _normal(current)
        if (not contact_exempt and CONTACT.search(text) or any(pattern.search(text) for pattern in _REFUSED)
                or any(pattern.search(text) for pattern in _PAYMENT)
                or any(pattern.search(text) for pattern in _RECRUITMENT)
                or any(pattern.search(text) for pattern in _PERSONAL)):
            raise AgentError(422, GOAL_SAFETY_REFUSAL)
        if tier == "sensitive":
            reasons.update(code for code, patterns in _HOLDS if any(pattern.search(text) for pattern in patterns))
    return {"held": bool(reasons), "reasons": [code for code in HOLD_CODES if code in reasons],
            "message": GOAL_SAFETY_HOLD if reasons else None}
