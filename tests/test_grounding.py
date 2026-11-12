from grounding import ground_updates


def test_grounding_keeps_only_items_with_matching_add_or_remove_intent():
    updates, rejected = ground_updates(
        "add olives but no mushrooms",
        {
            "add_toppings": ["olives", "mushrooms"],
            "remove_toppings": ["mushrooms"],
        },
        "pizza",
    )

    assert updates == {"add_toppings": ["olives"], "remove_toppings": ["mushrooms"]}
    assert rejected == []


def test_grounding_does_not_replace_items_without_replacement_intent():
    updates, rejected = ground_updates(
        "add olives",
        {"replace_toppings": ["olives"]},
        "pizza",
    )

    assert updates == {}
    assert rejected == ["replace_toppings"]


def test_grounding_discards_hallucinated_values():
    updates, rejected = ground_updates(
        "medium pizza",
        {"size": "large", "crust": "thin"},
        "pizza",
    )

    assert updates == {}
    assert sorted(rejected) == ["crust", "size"]


def test_grounding_rejects_negated_or_conflicting_scalar_choices():
    updates, rejected = ground_updates(
        "not medium, choose large instead",
        {"size": "medium"},
        "pizza",
    )
    assert updates == {}
    assert rejected == ["size"]

    updates, rejected = ground_updates(
        "medium, not large",
        {"size": "medium"},
        "pizza",
    )
    assert updates == {}
    assert rejected == ["size"]


def test_grounding_does_not_add_an_item_the_user_rejected():
    updates, rejected = ground_updates(
        "I don't want mushrooms; add olives",
        {"add_toppings": ["mushrooms", "olives"]},
        "pizza",
    )

    assert updates == {"add_toppings": ["olives"]}
    assert rejected == []


def test_grounding_requires_explicit_positive_confirmation():
    updates, rejected = ground_updates("don't confirm", {"confirmed": True}, "pizza")
    assert updates == {}
    assert rejected == ["confirmed"]

    updates, rejected = ground_updates("I will confirm later", {"confirmed": True}, "pizza")
    assert updates == {}
    assert rejected == ["confirmed"]

    updates, rejected = ground_updates("confirm", {"confirmed": True}, "pizza")
    assert updates == {"confirmed": True}
    assert rejected == []


def test_grounding_requires_acceptance_before_eta_override():
    updates, rejected = ground_updates("I don't accept the eta", {"approved_eta_override": True}, "pizza")

    assert updates == {}
    assert rejected == ["approved_eta_override"]


def test_grounding_checks_address_against_original_message():
    updates, rejected = ground_updates(
        "deliver to 10 Main St, NY",
        {"address": "10 Main St, Ny"},
        "pizza",
    )

    assert updates == {"address": "10 Main St, Ny"}
    assert rejected == []
