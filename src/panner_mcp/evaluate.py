"""Three-valued evaluation of frozen decision rules over per-citation answers.

This module is the single evaluation semantics shared by step 03 trial reads
and step 04 execution. Truth values are True (explicitly established),
False (explicitly contradicted), and None (undetermined). Undetermined never
produces an exclusion: silence, partial facts and conflicting answers leave
the citation retained.
"""

from __future__ import annotations

ANSWERS = ("hit", "partial", "miss", "unclear")
# Only an explicit answer can satisfy or contradict an expectation; partial and
# unclear answers always evaluate to None.
EXPLICIT = ("hit", "miss")


def _leaf(condition: dict, lookup) -> bool | None:
    answer = lookup(condition["signal"], condition["on"])
    if answer not in EXPLICIT:
        return None
    return answer == condition["expect"]


def _condition(condition: dict, lookup) -> bool | None:
    if "signal" in condition:
        return _leaf(condition, lookup)
    if "not" in condition:
        value = _condition(condition["not"], lookup)
        return None if value is None else not value
    kind = "all" if "all" in condition else "any"
    values = [_condition(child, lookup) for child in condition[kind]]
    if kind == "all":
        if any(value is False for value in values):
            return False
        if any(value is None for value in values):
            return None
        return True
    if any(value is True for value in values):
        return True
    if any(value is None for value in values):
        return None
    return False


def _answer_lookup(answers: list[dict]):
    by_key: dict[tuple[str, str], set[str]] = {}
    for item in answers:
        key = (item["signal"], item.get("on", "record"))
        by_key.setdefault(key, set()).add(item["answer"])

    def lookup(signal: str, on: str) -> str | None:
        found = by_key.get((signal, on))
        # Conflicting answers for the same object are treated as undetermined.
        if not found or len(found) != 1:
            return None
        return next(iter(found))

    return lookup


def _disqualifier_value(rule: dict, lookup, arms: list[str]) -> bool | None:
    condition = rule["condition"]
    if rule["unit"] == "record":
        return _condition(condition, lambda signal, on: lookup(signal, "record"))
    values = [_condition(condition, lambda signal, on, arm=arm: lookup(signal, arm)) for arm in arms]
    if rule["quantifier"] == "any":
        if any(value is True for value in values):
            return True
        if all(value is False for value in values):
            return False
        return None
    if any(value is False for value in values):
        return False
    if all(value is True for value in values):
        return True
    return None


def _qualification_value(rule: dict, lookup, arms: list[str]) -> bool | None:
    condition = rule["condition"]

    def for_pair(first: str, second: str) -> bool | None:
        resolved = {"member0": first, "member1": second, "record": "record"}

        def lookup_on(signal: str, on: str) -> str | None:
            return lookup(signal, resolved[on])

        return _condition(condition, lookup_on)

    values = [for_pair(first, second) for first in arms for second in arms if first != second]
    if any(value is True for value in values):
        return True
    # An empty or partially undetermined enumeration never proves that no
    # qualifying comparison exists.
    if values and all(value is False for value in values):
        return False
    return None


def evaluate_rules(rules: list[dict], judgment: dict) -> dict:
    """Evaluate frozen rules for one citation judgment.

    judgment carries the objects the citation names and the per-object signal
    answers, e.g. {"entities": {"arms": ["A1", "A2"]}, "answers":
    [{"signal": "S001", "on": "A1", "answer": "hit"}]}. Answers without "on"
    address the record itself.
    """
    arms = list(judgment.get("entities", {}).get("arms", []))
    lookup = _answer_lookup(judgment.get("answers", []))
    basis: list[dict] = []
    undetermined: list[dict] = []
    for rule in rules:
        if rule["kind"] == "disqualifier":
            value = _disqualifier_value(rule, lookup, arms)
            if value is True:
                basis.append(
                    {"rule": rule["id"], "kind": "disqualifier", "reason": "the disqualifying condition is explicitly satisfied"}
                )
            elif value is None:
                undetermined.append(
                    {"rule": rule["id"], "reason": "the disqualifying condition is not fully determined by the answers"}
                )
        else:
            value = _qualification_value(rule, lookup, arms)
            if value is True:
                continue
            if value is False:
                basis.append(
                    {
                        "rule": rule["id"],
                        "kind": "qualification",
                        "reason": "every candidate unit is explicitly disqualified, with the object enumeration complete",
                    }
                )
            else:
                undetermined.append(
                    {
                        "rule": rule["id"],
                        "reason": "a candidate unit may still qualify, or the enumeration of candidate units is incomplete",
                    }
                )
    return {"status": "excluded_candidate" if basis else "retained", "basis": basis, "undetermined": undetermined}
