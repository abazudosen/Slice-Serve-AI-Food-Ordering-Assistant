from __future__ import annotations

import re
import unicodedata
from typing import Any


def _term_variants(term: str) -> tuple[str, ...]:
    aliases = {
        "mushrooms": ("mushroom", "mushrooms"),
        "onions": ("onion", "onions"),
        "jalapeno": ("jalapeno", "jalapeño"),
    }
    return aliases.get(term, (term,))


def _contains_term(message: str, term: str) -> bool:
    terms = _term_variants(term)
    return any(re.search(rf"(?<!\w){re.escape(alias)}(?!\w)", message, re.IGNORECASE) for alias in terms)


def _has_replacement_intent(message: str, item_name: str) -> bool:
    return bool(
        re.search(
            rf"\b(?:change|replace|set|update)\s+{item_name}\b|\b{item_name}\b.{{0,24}}\binstead\b",
            message,
            re.IGNORECASE,
        )
    )


def _has_removal_intent(message: str) -> bool:
    return bool(
        re.search(
            r"\b(?:remove|without|no|hold|skip|leave out|don't want|do not want|dont want|"
            r"don't add|do not add|never add|exclude)\b",
            message,
            re.IGNORECASE,
        )
    )


def _item_has_removal_intent(message: str, item: str) -> bool:
    terms = _term_variants(item)
    for term in terms:
        for match in re.finditer(rf"(?<!\w){re.escape(term)}(?!\w)", message, re.IGNORECASE):
            preceding = message[max(0, match.start() - 48) : match.start()]
            preceding = re.split(r"[,.;]|\bbut\b|\band\b", preceding, flags=re.IGNORECASE)[-1]
            if re.search(r"\b(?:don't|do not|never)\s+(?:remove|exclude|skip)\b", preceding, re.IGNORECASE):
                continue
            if re.search(
                r"\b(?:remove|without|no|hold|skip|leave out|don't want|do not want|dont want|"
                r"don't add|do not add|never add|exclude)\b(?:\s+\w+){0,3}\s*$",
                preceding,
                re.IGNORECASE,
            ):
                return True
    return False


def _term_is_negated(message: str, term: str) -> bool:
    for variant in _term_variants(term):
        for match in re.finditer(rf"(?<!\w){re.escape(variant)}(?!\w)", message, re.IGNORECASE):
            preceding = message[max(0, match.start() - 48) : match.start()]
            preceding = re.split(r"[,.;]|\bbut\b|\band\b", preceding, flags=re.IGNORECASE)[-1]
            if re.search(
                r"\b(?:not|no|never|don't|do not|dont|can't|cannot|won't|rather than)\b"
                r"(?:\s+\w+){0,3}\s*$",
                preceding,
                re.IGNORECASE,
            ):
                return True
    return False


def _has_unambiguous_choice(message: str, value: str, choices: set[str]) -> bool:
    mentioned = {choice for choice in choices if _contains_term(message, choice)}
    return len(mentioned) == 1 and value in mentioned and not _term_is_negated(message, value)


def _has_positive_confirmation(message: str) -> bool:
    if re.search(
        r"\b(?:don't|do not|not|cannot|can't|won't|reject|cancel)\b.{0,32}\b(?:confirm|go ahead|place|order)\b",
        message,
        re.IGNORECASE,
    ):
        return False
    if re.search(r"\b(?:yes|yeah|yep)\b.{0,24}\b(?:wait|actually|change|cancel|reject)\b", message, re.IGNORECASE):
        return False
    if re.search(r"\bconfirm(?:ed)?\b.{0,32}\b(?:later|after|once|when|until|before)\b", message, re.IGNORECASE):
        return False
    return bool(
        re.search(
            r"\b(?:confirm|confirmed|go ahead|place (?:the )?order|yes|yeah|yep|looks good|that's correct|that is correct)\b",
            message,
            re.IGNORECASE,
        )
    )


def _has_rejection(message: str) -> bool:
    if re.search(r"\b(?:don't|do not|never)\s+(?:reject|cancel|stop)\b", message, re.IGNORECASE):
        return False
    return bool(re.search(r"\b(?:reject|cancel|stop|not correct)\b", message, re.IGNORECASE))


def _has_eta_approval(message: str) -> bool:
    if re.search(
        r"\b(?:don't|do not|not|cannot|can't|won't)\b.{0,24}\b(?:accept|approve|agree)\b",
        message,
        re.IGNORECASE,
    ):
        return False
    return bool(
        re.search(
            r"\b(?:accept|approve|agree to|okay with|ok with)\b.{0,24}\beta\b|\beta\b.{0,24}\b(?:accept|approve|agree)\b",
            message,
            re.IGNORECASE,
        )
    )


def _address_is_quoted_in_message(message: str, address: str) -> bool:
    def normalize(value: str) -> str:
        return " ".join(re.findall(r"\w+", unicodedata.normalize("NFKC", value).casefold()))

    normalized_address = normalize(address)
    return bool(normalized_address) and normalized_address in normalize(message)


def ground_updates(message: str, updates: dict[str, Any], flow: str) -> tuple[dict[str, Any], list[str]]:
    """Keep only schema-valid updates with evidence in this user's message."""
    grounded: dict[str, Any] = {}
    rejected: list[str] = []
    scalar_fields = (
        {"size", "crust", "payment_method", "address"}
        if flow == "pizza"
        else {"bread_type", "filling", "payment_method", "address"}
    )
    list_fields = (
        {"add_toppings", "replace_toppings", "remove_toppings", "allergies"}
        if flow == "pizza"
        else {"add_extras", "replace_extras", "remove_extras", "allergies"}
    )
    item_name = "toppings?" if flow == "pizza" else "extras?"
    scalar_choices = (
        {
            "size": {"small", "medium", "large"},
            "crust": {"thin", "thick", "gluten-free", "stuffed"},
            "payment_method": {"card", "cash", "swish", "paypal"},
        }
        if flow == "pizza"
        else {
            "bread_type": {"sesame", "plain", "everything", "whole wheat", "poppy seed"},
            "filling": {"cream cheese", "roast beef", "turkey", "salmon", "avocado", "ham"},
            "payment_method": {"card", "cash", "swish", "paypal"},
        }
    )

    for field in scalar_fields & updates.keys():
        value = updates[field]
        if isinstance(value, str) and (
            _address_is_quoted_in_message(message, value)
            if field == "address"
            else _has_unambiguous_choice(message, value, scalar_choices[field])
        ):
            grounded[field] = value
        else:
            rejected.append(field)

    for field in list_fields & updates.keys():
        value = updates[field]
        if not isinstance(value, list):
            rejected.append(field)
            continue
        if field == "allergies":
            accepted = [
                item
                for item in value
                if isinstance(item, str)
                and _contains_term(message, item)
                and not _term_is_negated(message, item)
            ]
        elif field.startswith("remove_"):
            accepted = (
                [
                    item
                    for item in value
                    if isinstance(item, str)
                    and _contains_term(message, item)
                    and _item_has_removal_intent(message, item)
                ]
                if _has_removal_intent(message)
                else []
            )
        elif field.startswith("replace_"):
            accepted = (
                [item for item in value if isinstance(item, str) and _contains_term(message, item)]
                if _has_replacement_intent(message, item_name)
                else []
            )
        else:
            accepted = (
                [
                    item
                    for item in value
                    if isinstance(item, str)
                    and _contains_term(message, item)
                    and not _term_is_negated(message, item)
                    and not _item_has_removal_intent(message, item)
                ]
                if not _has_replacement_intent(message, item_name)
                else []
            )
        if accepted:
            grounded[field] = accepted
        elif value:
            rejected.append(field)

    if updates.get("confirmed") is True and _has_positive_confirmation(message):
        grounded["confirmed"] = True
    elif "confirmed" in updates:
        rejected.append("confirmed")

    if updates.get("review_rejected") is True and _has_rejection(message):
        grounded["review_rejected"] = True
    elif "review_rejected" in updates:
        rejected.append("review_rejected")

    if updates.get("approved_eta_override") is True and _has_eta_approval(message):
        grounded["approved_eta_override"] = True
    elif "approved_eta_override" in updates:
        rejected.append("approved_eta_override")

    return grounded, rejected
