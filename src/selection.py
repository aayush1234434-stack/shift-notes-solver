# Python writes the menu. Granite picks the letter. A line with no accepted letter adds nothing.

import json
import re

from .extractor import (_reading_text, header_from_item, interpret_line,
                        note_line_numbers, numbered_lines)
from .schema import parse_problem

_LETTERS = "ABCDEFGH"
_ORDER = {"before", "after", "immediately_before", "immediately_after",
          "station_before_person", "station_after_person"}
_CHOICE = re.compile(r"(?m)^(?:line\s*)?(\d+)\s*[:.)-]?\s*([A-HX])\b", re.IGNORECASE)


def _describe(rule: dict) -> str:
    kind = rule["type"]
    if kind == "fixed_block":
        return f"{rule['person']} works the {rule['block']} block."
    if kind == "not_block":
        return f"{rule['person']} does not work the {rule['block']} block."
    if kind == "fixed_station":
        return f"{rule['person']} is on {rule['station']}."
    if kind == "not_station":
        return f"{rule['person']} is not on {rule['station']}."
    if kind == "before":
        return f"{rule['person']} is earlier than {rule['other_person']}."
    if kind == "after":
        return f"{rule['person']} is later than {rule['other_person']}."
    if kind == "immediately_before":
        return f"{rule['person']} is immediately earlier than {rule['other_person']}."
    if kind == "immediately_after":
        return f"{rule['person']} is immediately later than {rule['other_person']}."
    if kind == "adjacent":
        return f"{rule['person']} is next to {rule['other_person']}."
    if kind == "not_adjacent":
        return f"{rule['person']} is not next to {rule['other_person']}."
    if kind == "between":
        return (f"{rule['person']} is between {rule['other_person']} "
                f"and {rule['third_person']}.")
    if kind == "station_before_person":
        return f"The person on {rule['station']} is earlier than {rule['person']}."
    if kind == "station_after_person":
        return f"The person on {rule['station']} is later than {rule['person']}."
    return kind


def describe_rules(rules: list[dict]) -> str:
    return " ".join(_describe(rule) for rule in rules)


def _flip_order(rule: dict) -> dict | None:
    # Only order flips. "Not on 09:00" stays "not on 09:00"; X covers rejecting it.
    kind = rule["type"]
    if kind in {"before", "after", "immediately_before", "immediately_after"}:
        return {**rule, "person": rule["other_person"], "other_person": rule["person"]}
    if kind == "station_before_person":
        return {**rule, "type": "station_after_person"}
    if kind == "station_after_person":
        return {**rule, "type": "station_before_person"}
    return None


def _flip_rules(rules: list[dict]) -> list[dict] | None:
    flipped = []
    changed = False
    for rule in rules:
        other = _flip_order(rule)
        if other is None:
            flipped.append(dict(rule))
        else:
            flipped.append(other)
            changed = True
    if not changed:
        return None
    return flipped


def _signature(rules: list[dict]) -> str:
    return json.dumps(rules, sort_keys=True, ensure_ascii=False)


# Ordinary order language, not the visible note templates. A sentence can use
# none of those templates and still name a legal reading.
_ORDER_CUE = re.compile(
    r"\b(?:before|after|earlier|later|sooner|ahead|prior|previous|"
    r"follows|followed|precedes|preceded|preceding|following|between|"
    r"adjacent|beside|immediately|directly)\b",
    re.IGNORECASE)
_IMMEDIATE_CUE = re.compile(r"\b(?:immediately|directly)\b", re.IGNORECASE)


def _order_cue(text: str) -> bool:
    return _ORDER_CUE.search(text) is not None


def _generic_rules(line: str, header: dict) -> list[list[dict]]:
    # Fallback for wording the reader doesn't know: offer what the named entities allow.
    body = _reading_text(line)
    people = []
    for name in header["people"]:
        if re.search(rf"(?<!\w){re.escape(name)}(?!\w)", body, re.IGNORECASE):
            people.append((body.casefold().find(name.casefold()), name))
    people = [name for _, name in sorted(people)]
    blocks = [value for value in header["blocks"]
              if re.search(rf"(?<!\w){re.escape(value)}(?!\w)", body)]
    stations = [value for value in header["stations"]
                if re.search(rf"(?<!\w){re.escape(value)}(?!\w)", body, re.IGNORECASE)]
    holders = set(header["station_holders"])
    ordered = _order_cue(body)
    found = []
    if len(people) == 1 and len(blocks) == 1 and not stations:
        person, block = people[0], blocks[0]
        found.append([{"type": "fixed_block", "person": person, "block": block}])
        found.append([{"type": "not_block", "person": person, "block": block}])
    elif len(people) == 1 and len(stations) == 1 and not blocks:
        person, station = people[0], stations[0]
        if person in holders:
            found.append([{"type": "fixed_station", "person": person, "station": station}])
            found.append([{"type": "not_station", "person": person, "station": station}])
        if ordered:
            found.append([{"type": "station_before_person", "station": station, "person": person}])
            found.append([{"type": "station_after_person", "station": station, "person": person}])
    elif len(people) == 2 and not blocks and not stations and ordered:
        first, second = people
        pairs = [("before", first, second), ("before", second, first)]
        if _IMMEDIATE_CUE.search(body):
            pairs.extend((("immediately_before", first, second),
                          ("immediately_before", second, first)))
        for kind, person, other in pairs:
            found.append([{"type": kind, "person": person, "other_person": other}])
    elif len(people) == 3 and re.search(r"\bbetween\b", body, re.IGNORECASE):
        for middle in people:
            others = [name for name in people if name != middle]
            found.append([{"type": "between", "person": middle,
                           "other_person": others[0], "third_person": others[1]}])
    return found


def options_for_line(line: str, header: dict) -> list[dict]:
    bundles = []
    reading = interpret_line(line, header, decide_status=False)
    if reading and reading[0] == "rules":
        proposed = [dict(rule) for rule in reading[1]]
        bundles.append(proposed)
        flipped = _flip_rules(proposed)
        if flipped is not None and _signature(flipped) != _signature(proposed):
            bundles.append(flipped)
    else:
        bundles.extend(_generic_rules(line, header))
    options = []
    seen = set()
    for rules in bundles:
        signature = _signature(rules)
        if signature in seen:
            continue
        seen.add(signature)
        options.append({"key": _LETTERS[len(options)], "rules": rules,
                        "label": describe_rules(rules)})
        if len(options) == 4:
            break
    return options


def menus_for_item(item: dict) -> dict[int, list[dict]]:
    header = header_from_item(item)
    lines = numbered_lines(item)
    return {number: options_for_line(lines[number - 1], header)
            for number in note_line_numbers(item)}


def format_menu(item: dict, numbers: list[int] | None = None,
                chosen: dict[int, str] | None = None) -> str:
    lines = numbered_lines(item)
    menus = menus_for_item(item)
    selected = numbers if numbers is not None else list(menus)
    blocks = []
    for number in selected:
        options = menus[number]
        text = [f"Line {number}: {lines[number - 1]}"]
        if chosen and number in chosen:
            text.append(f"Current choice: {chosen[number]}")
        if not options:
            text.append("X: this line adds no current scheduling rule.")
        else:
            text.extend(f"{option['key']}: {option['label']}" for option in options)
            text.append("X: this line adds no current scheduling rule.")
        blocks.append("\n".join(text))
    return "\n\n".join(blocks)


def parse_choices(response: str) -> dict[int, str]:
    # If Granite answers a line twice, the last answer wins.
    text = response or ""
    choices = {}
    for match in _CHOICE.finditer(text):
        choices[int(match.group(1))] = match.group(2).upper()
    start, end = text.find("{"), text.rfind("}")
    if start >= 0 and end > start:
        try:
            data = json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            data = None
        rows = []
        if isinstance(data, dict):
            rows = data.get("selections") or data.get("choices") or []
        if isinstance(rows, list):
            for row in rows:
                if not isinstance(row, dict):
                    continue
                number, key = row.get("line", row.get("source_line")), row.get("option", row.get("choice"))
                if type(number) is int and isinstance(key, str) and re.fullmatch(r"[A-HX]", key, re.I):
                    choices[number] = key.upper()
    return choices


def problem_from_choices(item: dict, choices: dict[int, str], source_validation: bool = True):
    header = header_from_item(item)
    lines = numbered_lines(item)
    menus = menus_for_item(item)
    constraints = []
    ignored = []
    for number, options in menus.items():
        key = choices.get(number)
        if not options or key in (None, "X"):
            ignored.append(number)
            continue
        match = next((option for option in options if option["key"] == key), None)
        if match is None:
            ignored.append(number)
            continue
        for rule in match["rules"]:
            constraints.append({**rule, "source_line": number, "source": lines[number - 1]})
    data = {**header, "constraints": constraints, "ignored_lines": ignored}
    return parse_problem(data, raw_text=item["text"] if source_validation else None)


def review_messages(item: dict, choices: dict[int, str],
                    prompt_examples: bool = True) -> list[dict[str, str]]:
    from .extractor import selection_prompt
    return [
        {"role": "system", "content": selection_prompt(prompt_examples) +
         "\n\nA current choice is shown for lines already answered. "
         "Repeat a line only to change it. Keep a hedge as a rule. "
         "Change a line to X only when it is not a current rule."},
        {"role": "user", "content": format_menu(item, chosen=choices)},
    ]


def batch_messages(item: dict, numbers: list[int],
                   prompt_examples: bool = True) -> list[dict[str, str]]:
    from .extractor import header_from_item, selection_prompt
    header = header_from_item(item)
    return [
        {"role": "system", "content": selection_prompt(prompt_examples)},
        {"role": "user", "content": "Header lists:\n" +
         json.dumps(header, ensure_ascii=False) +
         "\n\nAnswer only these lines:\n\n" + format_menu(item, numbers)},
    ]


def selected_lines(choices: dict[int, str], menus: dict[int, list[dict]]) -> set[int]:
    accepted = set()
    for number, options in menus.items():
        key = choices.get(number)
        if key and key != "X" and any(option["key"] == key for option in options):
            accepted.add(number)
    return accepted
