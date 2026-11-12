from __future__ import annotations

import re
from abc import ABC, abstractmethod
from typing import Any

from app_models import BagelState, PizzaState, PlanStep
from grounding import ground_updates
from ollama_client import OllamaClient


class Planner(ABC):
    REQUIRED_FIELD_STEPS: list[tuple[str, str]] = []
    flow: str

    def __init__(self, llm: OllamaClient | None = None) -> None:
        self.llm = llm

    def extract_user_updates(self, message: str, state: PizzaState | BagelState | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
        if self.llm is None or state is None:
            llm_updates, llm_meta = {}, {
                "mode": "llm",
                "success": False,
                "error": "LLM extractor unavailable",
                "extractor_error": {"type": "unavailable"},
            }
        else:
            llm_updates, llm_meta = self._extract_with_llm(message, state)
        grounded_updates, filtered_fields = ground_updates(message, llm_updates, self.flow)
        if grounded_updates:
            return grounded_updates, {
                **llm_meta,
                "normalized_updates": grounded_updates,
                "filtered_ungrounded_fields": filtered_fields,
            }

        fallback_updates, fallback_filtered = ground_updates(
            message,
            self._fallback_extract_from_text(message),
            self.flow,
        )
        if fallback_updates:
            return fallback_updates, {
                "mode": "fallback",
                "success": True,
                "reason": "Used deterministic extraction because the model returned no grounded updates",
                "llm_extraction": llm_meta,
                "normalized_updates": fallback_updates,
                "filtered_ungrounded_fields": sorted(set(filtered_fields + fallback_filtered)),
            }

        return {}, {
            **llm_meta,
            "success": False,
            "error": llm_meta.get("error") or "No supported order detail was found in the message",
            "filtered_ungrounded_fields": sorted(set(filtered_fields + fallback_filtered)),
        }

    @abstractmethod
    def _extract_with_llm(self, message: str, state: PizzaState | BagelState) -> tuple[dict[str, Any], dict[str, Any]]:
        ...

    @abstractmethod
    def _fallback_extract_from_text(self, message: str) -> dict[str, Any]:
        ...

    def build_plan(self, state: PizzaState | BagelState) -> list[PlanStep]:
        plan: list[PlanStep] = []

        for field_name, step_name in self.REQUIRED_FIELD_STEPS:
            value = getattr(state, field_name)
            is_missing = value is None or (isinstance(value, list) and len(value) == 0)
            if is_missing:
                plan.append(PlanStep(step_name))

        if plan:
            return plan

        plan.append(PlanStep("check_constraints"))

        if state.review_rejected:
            plan.append(PlanStep("ask_changes"))
            return plan

        if not state.confirmed:
            plan.append(PlanStep("ask_confirm"))
            return plan

        plan.append(PlanStep("charge_payment"))
        plan.append(PlanStep("place_order"))
        return plan

    def missing_requirements(self, state: PizzaState | BagelState) -> list[str]:
        missing: list[str] = []

        for field_name, _ in self.REQUIRED_FIELD_STEPS:
            value = getattr(state, field_name)
            is_missing = value is None or (isinstance(value, list) and len(value) == 0)
            if is_missing:
                missing.append(field_name)

        if not state.confirmed:
            missing.append("confirmation")
        return missing

    def _looks_like_address(self, message: str) -> bool:
        raw = message.strip()
        if len(raw) < 6:
            return False

        has_number = bool(re.search(r"\d", raw))
        has_word = bool(re.search(r"[A-Za-z]", raw))
        looks_structured = "," in raw or bool(re.search(r"\b(st|street|ave|avenue|road|rd|ny)\b", raw.lower()))

        return has_number and has_word and looks_structured


class PizzaPlanner(Planner):
    flow = "pizza"
    SIZES = {"small", "medium", "large"}
    CRUSTS = {"thin", "thick", "gluten-free", "stuffed"}
    PAYMENT = {"card", "cash", "swish", "paypal"}
    TOPPING_VOCAB = [
        "pepperoni",
        "mushroom",
        "mushrooms",
        "onion",
        "onions",
        "olives",
        "ham",
        "pineapple",
        "cheese",
        "jalapeno",
        "bacon",
        "tomato",
        "anchovy",
        "paprika",
    ]
    TOPPING_CANONICAL = {
        "mushroom": "mushrooms",
        "mushrooms": "mushrooms",
        "onion": "onions",
        "onions": "onions",
        "jalapeño": "jalapeno",
    }
    REQUIRED_FIELD_STEPS = [
        ("size", "ask_size"),
        ("crust", "ask_crust"),
        ("toppings", "ask_toppings"),
        ("address", "ask_address"),
        ("payment_method", "ask_payment"),
    ]

    def _extract_with_llm(self, message: str, state: PizzaState | BagelState) -> tuple[dict[str, Any], dict[str, Any]]:
        if not isinstance(state, PizzaState):
            return {}, {"mode": "llm", "success": False, "error": "Invalid state for PizzaPlanner"}
        return self.llm.extract_order_update(message, state)

    def _fallback_extract_from_text(self, message: str) -> dict[str, Any]:
        m = message.lower()
        updates: dict[str, Any] = {}

        for size in self.SIZES:
            if re.search(rf"\b{re.escape(size)}\b", m):
                updates["size"] = size

        for crust in self.CRUSTS:
            if re.search(rf"\b{re.escape(crust)}\b", m):
                updates["crust"] = crust

        payments_map = {
            "credit card": "card",
            "debit card": "card",
            "card": "card",
            "cash": "cash",
            "swish": "swish",
            "paypal": "paypal",
        }
        for key, value in payments_map.items():
            if key in m:
                updates["payment_method"] = value

        if re.search(r"\b(?:confirm|confirmed|go ahead|place (?:the )?order|yes|yeah|yep)\b", m):
            updates["confirmed"] = True

        if re.search(r"\b(?:reject|cancel|stop)\b", m):
            updates["review_rejected"] = True

        if "accept" in m and "eta" in m:
            updates["approved_eta_override"] = True
        if "override" in m and "eta" in m:
            updates["approved_eta_override"] = True

        allergy_terms = ["nuts", "peanuts", "gluten", "dairy", "lactose"]
        allergies = [term for term in allergy_terms if re.search(rf"\b{term}\b", m)]
        if allergies:
            updates["allergies"] = sorted(set(allergies))

        address_match = re.search(r"(?:deliver to|address is)\s+([a-z0-9][a-z0-9\s,\-]{5,})", m)
        if address_match:
            updates["address"] = address_match.group(1).strip().title()
        elif self._looks_like_address(message):
            updates["address"] = message.strip().title()

        replace_match = re.search(r"(?:change|set|update)\s+toppings?\s+(?:to|with)\s+(.+)", m)
        if replace_match:
            replacement_text = replace_match.group(1)
            replacement = [
                topping for topping in self.TOPPING_VOCAB if re.search(rf"\b{re.escape(topping)}\b", replacement_text)
            ]
            if replacement:
                updates["replace_toppings"] = self._canonicalize_toppings(replacement)

        removed = []
        remove_patterns = [
            r"remove\s+([a-z\s,]+)",
            r"without\s+([a-z\s,]+)",
            r"no\s+([a-z\s,]+)",
            r"(?:don't|do not|dont) want\s+([a-z\s,]+)",
            r"(?:don't|do not|never) add\s+([a-z\s,]+)",
            r"exclude\s+([a-z\s,]+)",
        ]
        for pattern in remove_patterns:
            remove_match = re.search(pattern, m)
            if remove_match:
                removed.extend(
                    [
                        topping
                        for topping in self.TOPPING_VOCAB
                        if re.search(rf"\b{re.escape(topping)}\b", remove_match.group(1))
                    ]
                )
        if removed:
            updates["remove_toppings"] = self._canonicalize_toppings(removed)

        picked = [topping for topping in self.TOPPING_VOCAB if re.search(rf"\b{re.escape(topping)}\b", m)]
        if picked and "replace_toppings" not in updates:
            updates["add_toppings"] = self._canonicalize_toppings(picked)

        return updates

    def _canonicalize_toppings(self, toppings: list[str]) -> list[str]:
        canonical = [self.TOPPING_CANONICAL.get(topping, topping) for topping in toppings]
        return sorted(set(canonical))


class BagelPlanner(Planner):
    flow = "bagel"
    BREAD_TYPES = {"sesame", "plain", "everything", "whole wheat", "poppy seed"}
    FILLINGS = {"cream cheese", "roast beef", "turkey", "salmon", "avocado", "ham"}
    EXTRAS = {"onion", "tomato", "lettuce", "cucumber", "capers", "jalapeno", "cheese"}
    PAYMENT = {"card", "cash", "swish", "paypal"}
    REQUIRED_FIELD_STEPS = [
        ("bread_type", "ask_bread_type"),
        ("filling", "ask_filling"),
        ("extras", "ask_extras"),
        ("address", "ask_address"),
        ("payment_method", "ask_payment"),
    ]

    def _extract_with_llm(self, message: str, state: PizzaState | BagelState) -> tuple[dict[str, Any], dict[str, Any]]:
        if not isinstance(state, BagelState):
            return {}, {"mode": "llm", "success": False, "error": "Invalid state for BagelPlanner"}
        return self.llm.extract_bagel_update(message, state)

    def _fallback_extract_from_text(self, message: str) -> dict[str, Any]:
        m = message.lower()
        updates: dict[str, Any] = {}

        for bread in self.BREAD_TYPES:
            if re.search(rf"\b{re.escape(bread)}\b", m):
                updates["bread_type"] = bread

        for filling in self.FILLINGS:
            if re.search(rf"\b{re.escape(filling)}\b", m):
                updates["filling"] = filling

        payments_map = {
            "credit card": "card",
            "debit card": "card",
            "card": "card",
            "cash": "cash",
            "swish": "swish",
            "paypal": "paypal",
        }
        for key, value in payments_map.items():
            if key in m:
                updates["payment_method"] = value

        if re.search(r"\b(?:confirm|confirmed|go ahead|place (?:the )?order|yes|yeah|yep)\b", m):
            updates["confirmed"] = True

        if re.search(r"\b(?:reject|cancel|stop)\b", m):
            updates["review_rejected"] = True

        if "accept" in m and "eta" in m:
            updates["approved_eta_override"] = True
        if "override" in m and "eta" in m:
            updates["approved_eta_override"] = True

        allergy_terms = ["nuts", "peanuts", "gluten", "dairy", "lactose"]
        allergies = [term for term in allergy_terms if re.search(rf"\b{term}\b", m)]
        if allergies:
            updates["allergies"] = sorted(set(allergies))

        address_match = re.search(r"(?:deliver to|address is)\s+([a-z0-9][a-z0-9\s,\-]{5,})", m)
        if address_match:
            updates["address"] = address_match.group(1).strip().title()
        elif self._looks_like_address(message):
            updates["address"] = message.strip().title()

        replace_match = re.search(r"(?:change|set|update)\s+extras?\s+(?:to|with)\s+(.+)", m)
        if replace_match:
            replacement_text = replace_match.group(1)
            replacement = [
                extra for extra in self.EXTRAS if re.search(rf"\b{re.escape(extra)}\b", replacement_text)
            ]
            if replacement:
                updates["replace_extras"] = sorted(set(replacement))

        removed = []
        remove_patterns = [
            r"remove\s+([a-z\s,]+)",
            r"without\s+([a-z\s,]+)",
            r"no\s+([a-z\s,]+)",
            r"(?:don't|do not|dont) want\s+([a-z\s,]+)",
            r"(?:don't|do not|never) add\s+([a-z\s,]+)",
            r"exclude\s+([a-z\s,]+)",
        ]
        for pattern in remove_patterns:
            remove_match = re.search(pattern, m)
            if remove_match:
                removed.extend(
                    [
                        extra
                        for extra in self.EXTRAS
                        if re.search(rf"\b{re.escape(extra)}\b", remove_match.group(1))
                    ]
                )
        if removed:
            updates["remove_extras"] = sorted(set(removed))

        picked = [extra for extra in self.EXTRAS if re.search(rf"\b{re.escape(extra)}\b", m)]
        if picked and "replace_extras" not in updates:
            updates["add_extras"] = sorted(set(picked))

        return updates
