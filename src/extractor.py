import json
import re
from pathlib import Path

from .schema import SchemaError, parse_problem

ROOT = Path(__file__).resolve().parents[1]
ORDER_CONNECTOR = re.compile(
    r"\b(immediately before|directly before|right before|just before|"
    r"earlier in the day than|earlier than|ahead of|prior to|precedes|before|"
    r"immediately after|directly after|right after|just after|"
    r"later in the day than|later than|follows|after)\b", re.IGNORECASE)
_EARLIER = {"before", "precedes", "earlier than", "earlier in the day than",
            "immediately before", "directly before", "right before", "just before",
            "ahead of", "prior to"}
_HEDGE = re.compile(
    r"^(?:(?:but|and|also|so|well)\b[:,]?\s+)*"
    r"(?:it bears repeating|worth restating(?:,\s*since it came up twice in handover)?|"
    r"noted twice in the handover|this came up more than once,\s*so)\s*[:,]?\s*"
    r"|^(?:(?:but|and|also|so|well)\b[:,]?\s+)*"
    r"(?:(?:i'm|i am|we're|we are)\s+)?"
    r"(?:fairly sure|pretty sure|quite sure|speaking from memory|going off the roster|"
    r"as far as i know|my recollection is that|as i recall|from memory|i recall|"
    r"i think|i believe|apparently|reportedly|"
    r"so far as i know|so far as i remember|if i remember(?: correctly)?)"
    r"[:,]?\s*(?:that\s+)?", re.IGNORECASE)
_UNSETTLED = re.compile(
    r"\b(?:whether|nothing was minuted|left open|disagreement about|"
    r"could(?: not|n't) remember|nobody could remember|no one could remember)\b",
    re.IGNORECASE)
_BLOCK_CUE = re.compile(
    r"\b(works?|working|down for|assigned|assignment|scheduled|rostered|"
    r"starts?|given|covers?|covering|is at|on the block|(?:is|was)(?:\s+not)?\s+on)\b",
    re.IGNORECASE)
_STATION_CUE = re.compile(
    r"\b(works?|working|assigned|assignment|scheduled|rostered|"
    r"covers?|covering|put on|(?:is|was|been)(?:\s+not)?\s+on|given|down for)\b",
    re.IGNORECASE)
_NEGATION = re.compile(r"\b(?:not|no|never|without)\b|n't\b", re.IGNORECASE)
NONCURRENT = re.compile(
    r"\b(last month|previous cycle|old arrangement|back in the old|"
    r"in the spring|would have|had the rota gone|lobbied for|wanted|"
    r"put in for|asked to move|request was declined|without success)\b",
    re.IGNORECASE)
VISIBLE_HEADER = re.compile(
    r"(?:[^.\n]*\.\s*)?(?P<count>\d+) staff on the rota: (?P<people>.*?)\. Blocks run "
    r"(?P<blocks>.*?), one person per block, and each person works exactly one block\. "
    r"There are (?P<station_count>\d+) stations, one person on each: "
    r"(?P<stations>.*?)\. The people on a station are (?P<station_holders>.*?); "
    r"the rest are on no station\."
)
SIMPLE_HEADER = re.compile(
    r"Staff: (?P<people>.*?)\. Blocks: (?P<blocks>.*?), one person each\. "
    r"Station holders: (?P<station_holders>.*?)\. Stations: "
    r"(?P<stations>.*?), one person each\."
)


def numbered_lines(item: dict) -> list[str]:
    return [line for line in item["text"].splitlines() if line.strip()]


def _header_match(item: dict):
    matches = [(number, match) for number, line in enumerate(numbered_lines(item), 1)
               if (match := VISIBLE_HEADER.fullmatch(line) or SIMPLE_HEADER.fullmatch(line))]
    if len(matches) != 1:
        raise SchemaError(f"Expected exactly one complete rota header line; found {len(matches)}")
    return matches[0]


def header_line_number(item: dict) -> int:
    return _header_match(item)[0]


def note_line_numbers(item: dict) -> list[int]:
    header_number = header_line_number(item)
    return [number for number in range(1, len(numbered_lines(item)) + 1)
            if number != header_number]


def header_from_item(item: dict) -> dict[str, list[str]]:
    # Parsed here, not by Granite: asked to copy the staff list, it invented names.
    _, match = _header_match(item)
    values = {name: match[name].split(", ")
              for name in ("people", "blocks", "stations", "station_holders")}
    if match.re is VISIBLE_HEADER and (
        len(values["people"]) != int(match["count"])
        or len(values["stations"]) != int(match["station_count"])
    ):
        raise SchemaError("Header counts do not match its lists")
    for field, expected in (("n_staff", len(values["people"])),
                            ("n_stations", len(values["stations"]))):
        if field in item and item[field] != expected:
            raise SchemaError(f"Header disagrees with {field}")
    parse_problem({**values, "constraints": []})
    return values


def _entities_in(text: str, values: list[str]) -> list[str]:
    return [value for value in values
            if re.search(r"(?<!\w)" + re.escape(value) + r"(?!\w)", text, re.IGNORECASE)]


def _mentioned(text: str, values: list[str]) -> list[str]:
    found = []
    for value in values:
        match = re.search(r"(?<!\w)" + re.escape(value) + r"(?!\w)", text, re.IGNORECASE)
        if match:
            found.append((match.start(), value))
    return [value for _, value in sorted(found)]


def _unambiguous_order(line: str, header: dict) -> dict | None:
    people = header["people"]
    stations = header["stations"]
    between_match = re.search(r"\b(after|before) one of\b", line, re.IGNORECASE)
    if between_match and re.search(r"\band (?:before|after) the other\b", line, re.IGNORECASE):
        subject = _entities_in(line[:between_match.start()], people)
        others = _entities_in(line[between_match.end():], people)
        if len(subject) == 1 and len(others) == 2:
            return {"type": "between", "person": subject[0],
                    "other_person": others[0], "third_person": others[1]}
    if "no block between" in line.casefold() and "in that order" in line.casefold():
        named = [(line.find(name), name) for name in people if name in line]
        if len(named) == 2:
            first, second = [name for _, name in sorted(named)]
            return {"type": "immediately_before", "person": first, "other_person": second}
    match = ORDER_CONNECTOR.search(line)
    if match is None:
        return None
    # "Not before" and similar negations are not positive precedence claims.
    if re.search(r"\bnot\s+$", line[max(0, match.start() - 12):match.start()], re.IGNORECASE):
        return None
    left, right = line[:match.start()], line[match.end():]
    left_people, right_people = _entities_in(left, people), _entities_in(right, people)
    left_stations, right_stations = _entities_in(left, stations), _entities_in(right, stations)
    if len(left_people) + len(right_people) != 2 and not (
        len(left_people) + len(right_people) == 1
        and len(left_stations) + len(right_stations) == 1
    ):
        return None
    earlier = match.group(1).casefold() in _EARLIER
    immediate = any(word in match.group(1).casefold()
                    for word in ("immediately", "directly", "right", "just"))
    if len(left_people) == len(right_people) == 1 and not left_stations and not right_stations:
        first = left_people[0] if earlier else right_people[0]
        second = right_people[0] if earlier else left_people[0]
        return {"type": "immediately_before" if immediate else "before",
                "person": first, "other_person": second}
    if len(left_stations) == len(right_people) == 1 and not left_people and not right_stations:
        return {"type": "station_before_person" if earlier else "station_after_person",
                "station": left_stations[0], "person": right_people[0]}
    if len(left_people) == len(right_stations) == 1 and not left_stations and not right_people:
        return {"type": "station_after_person" if earlier else "station_before_person",
                "station": right_stations[0], "person": left_people[0]}
    return None


def _reading_text(line: str) -> str:
    # Only for reading the rule. The citation keeps the hedge: the brief wants the full line.
    text = line.strip()
    previous = None
    while text != previous:
        previous = text
        text = _HEDGE.sub("", text)
    return text


def _symmetric_people(line: str, header: dict, kind: str) -> dict | None:
    people = _entities_in(line, header["people"])
    if len(people) != 2:
        return None
    return {"type": kind, "person": people[0], "other_person": people[1]}


def _extra_relation(line: str, header: dict) -> dict | None:
    if re.search(r"\bnot\s+(?:adjacent(?:\s+to)?|next to|beside)\b", line, re.IGNORECASE):
        return _symmetric_people(line, header, "not_adjacent")
    if (re.search(r"\b(?:adjacent to|next to|beside)\b", line, re.IGNORECASE)
            and not _NEGATION.search(line)):
        return _symmetric_people(line, header, "adjacent")
    between = re.search(r"\bbetween\b", line, re.IGNORECASE)
    if between is None:
        return None
    names = header["people"]
    mentioned = _entities_in(line, names)
    if len(mentioned) != 3:
        return None
    before = _entities_in(line[:between.start()], names)
    after = _entities_in(line[between.end():], names)
    if len(before) == 1 and len(after) == 2:
        return {"type": "between", "person": before[0],
                "other_person": after[0], "third_person": after[1]}
    if re.search(r"\bbetween\s+them\b", line, re.IGNORECASE):
        subject = max(mentioned, key=line.rfind)
        others = [name for name in mentioned if name != subject]
        return {"type": "between", "person": subject,
                "other_person": others[0], "third_person": others[1]}
    return None


def _link_window(text: str, left: str, right: str) -> str | None:
    found = [re.search(rf"(?<!\w){re.escape(value)}(?!\w)", text) for value in (left, right)]
    if any(match is None for match in found):
        return None
    first, second = sorted(found, key=lambda match: match.start())
    return text[first.start():second.end()]


def _assignment_rules(body: str, header: dict, people: list[str],
                      blocks: list[str], stations: list[str]) -> list[dict]:
    if len(people) != 1:
        return []
    person = people[0]
    # A line that names both a block and a station can scope a negation either way.
    if len(blocks) == 1 and len(stations) == 1:
        return []
    holder = person in header["station_holders"]
    block_window = _link_window(body, person, blocks[0]) if len(blocks) == 1 else None
    station_window = _link_window(body, person, stations[0]) if len(stations) == 1 else None
    block_ok = block_window is not None and _BLOCK_CUE.search(block_window) is not None
    station_ok = (station_window is not None and holder
                  and _STATION_CUE.search(station_window) is not None)
    if block_ok:
        negated = _NEGATION.search(block_window) is not None
        return [{"type": "not_block" if negated else "fixed_block",
                 "person": person, "block": blocks[0]}]
    if station_ok:
        negated = _NEGATION.search(station_window) is not None
        return [{"type": "not_station" if negated else "fixed_station",
                 "person": person, "station": stations[0]}]
    return []


def _pair_around(text: str, header: dict, verb: str):
    match = re.search(verb, text, re.IGNORECASE)
    if match is None:
        return None
    left = _mentioned(text[:match.start()], header["people"])
    right = _mentioned(text[match.end():], header["people"])
    if len(left) == 1 and len(right) == 1:
        return left[0], right[0]
    return None


def _known_idiom(body: str, header: dict):
    people = _mentioned(body, header["people"])
    blocks = _entities_in(body, header["blocks"])
    stations = _entities_in(body, header["stations"])
    holders = set(header["station_holders"])

    def block_rule(kind: str):
        if len(people) == 1 and len(blocks) == 1 and not stations:
            return ("rules", [{"type": kind, "person": people[0], "block": blocks[0]}])
        return None

    def station_rule(kind: str):
        if len(people) == 1 and people[0] in holders and len(stations) == 1 and not blocks:
            return ("rules", [{"type": kind, "person": people[0], "station": stations[0]}])
        return None

    if re.search(r"\brules out the\b|\bunavailable at\b|\bwill not find\b", body, re.IGNORECASE):
        return block_rule("not_block")
    if re.search(r"\bdefinitely not on\b", body, re.IGNORECASE) and blocks:
        return block_rule("not_block")
    if re.search(r"\bblock is not\b", body, re.IGNORECASE):
        return block_rule("not_block")
    if re.search(r"\bblock is\b", body, re.IGNORECASE) and re.search(r"'s\b", body):
        found = block_rule("fixed_block")
        if found:
            return found
    if re.search(r"\bwhoever drew the\b", body, re.IGNORECASE) and re.search(r"\bit was\b", body, re.IGNORECASE):
        return block_rule("fixed_block")
    if re.search(r"\btakes\b", body, re.IGNORECASE) and re.search(r"\bas things stand\b", body, re.IGNORECASE):
        return block_rule("fixed_block")
    if re.search(r"\bopens up at\b", body, re.IGNORECASE):
        return block_rule("fixed_block")
    if re.search(r"\bis when\b", body, re.IGNORECASE) and re.search(r"\bscheduled\b", body, re.IGNORECASE):
        return block_rule("fixed_block")
    if re.search(r"\brule\b", body, re.IGNORECASE) and re.search(r"\bout for\b", body, re.IGNORECASE):
        return station_rule("not_station")
    if re.search(r"\bcovered by someone other than\b", body, re.IGNORECASE):
        return station_rule("not_station")
    if re.search(r"\bdown to\b", body, re.IGNORECASE):
        return station_rule("fixed_station")
    if re.search(r"\bis not\b", body, re.IGNORECASE) and re.search(r"\bstation\b", body, re.IGNORECASE):
        found = station_rule("not_station")
        if found:
            return found
    if re.search(r"'s station\b", body, re.IGNORECASE):
        found = station_rule("fixed_station")
        if found:
            return found
    if re.search(r"\bthis week\b", body, re.IGNORECASE):
        found = station_rule("fixed_station")
        if found:
            return found
    if (re.search(r"\bcovered by\b", body, re.IGNORECASE)
            and not re.search(r"\bsomeone other than\b|\bbefore\b", body, re.IGNORECASE)):
        found = station_rule("fixed_station")
        if found:
            return found

    relieved = _pair_around(body, header, r"\brelieves\b")
    if relieved and re.search(r"\b(?:directly|no block in between)\b", body, re.IGNORECASE):
        earlier, later = relieved[1], relieved[0]
        return ("rules", [{"type": "immediately_before", "person": earlier, "other_person": later}])
    takeover = _pair_around(body, header, r"\btakes over from\b")
    if takeover:
        earlier, later = takeover[1], takeover[0]
        return ("rules", [{"type": "before", "person": earlier, "other_person": later}])
    handover = _pair_around(body, header, r"\bhands straight over to\b")
    if handover:
        return ("rules", [{"type": "immediately_before", "person": handover[0], "other_person": handover[1]}])
    back_to_back = _pair_around(body, header, r"\bthen\b")
    if back_to_back and re.search(r"\bback to back\b", body, re.IGNORECASE):
        return ("rules", [{"type": "immediately_before",
                           "person": back_to_back[0], "other_person": back_to_back[1]}])
    if (re.search(r"\bby the time\b", body, re.IGNORECASE)
            and re.search(r"\balready been on\b", body, re.IGNORECASE) and len(people) == 2):
        later, earlier = people[0], people[1]
        return ("rules", [{"type": "before", "person": earlier, "other_person": later}])
    return None


def interpret_clause(line: str, header: dict, decide_status: bool = True):
    # decide_status=False keeps "last month" and "whether" lines on the menu, so Granite
    # is the one that rejects them.
    body = _reading_text(line)
    if not body:
        return ("ignore", [])
    if decide_status and _UNSETTLED.search(body):
        return ("ignore", [])
    idiom = _known_idiom(body, header)
    if idiom is not None:
        return idiom
    people = _entities_in(body, header["people"])
    blocks = _entities_in(body, header["blocks"])
    stations = _entities_in(body, header["stations"])
    if not people and not blocks and not stations:
        return ("ignore", [])
    connector = ORDER_CONNECTOR.search(body)
    if connector and re.search(r"\b(?:not|never)\s+$",
                               body[max(0, connector.start() - 16):connector.start()],
                               re.IGNORECASE):
        return None
    relation = _unambiguous_order(body, header)
    if relation is None:
        relation = _extra_relation(body, header)
    if relation is not None:
        return ("rules", [relation])
    if connector is not None:
        return None
    rules = _assignment_rules(body, header, people, blocks, stations)
    if rules:
        return ("rules", rules)
    # No time, station, or order word. These are the social asides. Keeping them
    # out of the model’s hands stops a filler sentence from becoming a rule.
    if not blocks and not stations and connector is None and not re.search(r"\bbetween\b", body, re.IGNORECASE):
        return ("ignore", [])
    return None


def interpret_line(line: str, header: dict, decide_status: bool = True):
    parts = [part.strip() for part in re.split(r";\s*|(?<=[.!?])\s+", line) if part.strip()]
    if len(parts) <= 1:
        if decide_status and NONCURRENT.search(line):
            return ("ignore", [])
        return interpret_clause(line, header, decide_status)
    rules = []
    for part in parts:
        if decide_status and NONCURRENT.search(part):
            continue
        reading = interpret_clause(part, header, decide_status)
        has_entities = bool(_entities_in(part, header["people"])
                            or _entities_in(part, header["blocks"])
                            or _entities_in(part, header["stations"]))
        if reading is None and has_entities:
            return None
        if reading is not None and reading[0] == "rules":
            rules.extend(reading[1])
    if rules:
        return ("rules", rules)
    return ("ignore", [])


def model_cited_lines(response: str) -> set[int]:
    text = (response or "").strip()
    if text.startswith("```json\n") and text.endswith("\n```"):
        text = text[len("```json\n"):-len("\n```")]
    elif text.startswith("```\n") and text.endswith("\n```"):
        text = text[len("```\n"):-len("\n```")]
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            return set()
        try:
            data = json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            return set()
    if not isinstance(data, dict):
        return set()
    found = set()
    rules = list(data.get("constraints") or [])
    for edit in data.get("edits") or []:
        if isinstance(edit, dict):
            rules.extend(edit.get("constraints") or [])
    for rule in rules:
        if isinstance(rule, dict) and type(rule.get("source_line")) is int:
            found.add(rule["source_line"])
    return found


def selection_prompt(prompt_examples: bool = True) -> str:
    prompt = (ROOT / "prompts" / "extract.txt").read_text(encoding="utf-8")
    if not prompt_examples:
        start = prompt.index("Generic examples of language meanings")
        end = prompt.index("Extract every definite constraint", start)
        prompt = prompt[:start] + prompt[end:]
    return prompt


def build_messages(item: dict, prompt_examples: bool = True) -> list[dict[str, str]]:
    from .selection import format_menu
    header = header_from_item(item)
    return [
        {"role": "system", "content": selection_prompt(prompt_examples)},
        {"role": "user", "content": "Header lists:\n" +
         json.dumps(header, ensure_ascii=False) + "\n\n" + format_menu(item)},
    ]


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise SchemaError(f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_constant(value):
    raise SchemaError(f"Invalid JSON constant: {value}")


def decode_extraction(response: str, item: dict, source_validation: bool = True,
                      require_complete: bool = True):
    """Turn a constraint JSON object into a problem. ./run does not call this.

    The live path is letter selection. This remains so the example JSON files and
    the source-check ablation can still be validated.
    """
    text = response.strip()
    if text.startswith("```json\n") and text.endswith("\n```"):
        text = text[len("```json\n"):-len("\n```")]
    elif text.startswith("```\n") and text.endswith("\n```"):
        text = text[len("```\n"):-len("\n```")]
    try:
        data = json.loads(text, object_pairs_hook=_unique_object, parse_constant=_reject_constant)
    except json.JSONDecodeError as exc:
        raise SchemaError(f"Malformed extraction JSON at character {exc.pos}") from None
    if not isinstance(data, dict):
        raise SchemaError("Extraction must be a JSON object")
    header = header_from_item(item)
    data = dict(data)
    for name, values in header.items():
        if name in data and data[name] != values:
            raise SchemaError(f"Extracted {name} disagrees with the header")
        data[name] = values
    if not isinstance(data.get("constraints"), list):
        raise SchemaError("constraints must be a list")
    if not isinstance(data.get("ignored_lines"), list):
        raise SchemaError("ignored_lines must be an explicit list")
    lines = numbered_lines(item)
    note_lines = set(note_line_numbers(item))
    ignored = data["ignored_lines"]
    if (any(type(number) is not int or number not in note_lines for number in ignored)
            or len(ignored) != len(set(ignored))):
        raise SchemaError("ignored_lines must contain unique non-header line numbers")
    cited = set()
    for index, rule in enumerate(data["constraints"]):
        if not isinstance(rule, dict) or type(rule.get("source_line")) is not int or rule["source_line"] not in note_lines:
            raise SchemaError(f"Constraint {index} has an invalid non-header source_line")
        cited.add(rule["source_line"])
    overlap = cited & set(ignored)
    if overlap:
        raise SchemaError(f"Lines both constrained and ignored: {sorted(overlap)}")
    if require_complete and note_lines - cited - set(ignored):
        raise SchemaError(f"Unclassified note lines: {sorted(note_lines - cited - set(ignored))}")
    if source_validation:
        constraints = []
        seen = set()
        for index, raw_rule in enumerate(data["constraints"]):
            if not isinstance(raw_rule, dict):
                raise SchemaError(f"Constraint {index} must be an object")
            number, evidence = raw_rule.get("source_line"), raw_rule.get("source")
            if type(number) is not int or number not in note_lines:
                raise SchemaError(f"Constraint {index} has an invalid non-header source_line")
            line = lines[number - 1]
            if not isinstance(evidence, str) or not evidence.strip() or evidence not in line:
                raise SchemaError(f"Constraint {index} has evidence absent from line {number}")
            rule = {**raw_rule, "source": line}
            parse_problem({**header, "constraints": [rule]}, raw_text=item["text"])
            signature = json.dumps(rule, sort_keys=True)
            if signature not in seen:
                constraints.append(rule)
                seen.add(signature)
        data["constraints"] = constraints
    problem = parse_problem(data, raw_text=item["text"] if source_validation else None)
    for name, values in (("people", problem.people), ("blocks", problem.blocks),
                         ("stations", problem.stations), ("station_holders", problem.station_holders)):
        if any(value not in item["text"] for value in values):
            raise SchemaError(f"{name} contains strings absent from the original notes")
    for field, actual in (("n_staff", len(problem.people)), ("n_stations", len(problem.stations))):
        if field in item and item[field] != actual:
            raise SchemaError(f"Extracted header disagrees with {field}")
    return problem
