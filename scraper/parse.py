"""Parse the CMTEB outage table into one row per (table row, punct termic, street).

Stdlib only. Where two reasonable readings of the page exist, takes the more
tolerant path:

* table selection -- the first `<table class="... raport">` inside the
  "Toate sectoarele" tab (`div#ST`); the six per-sector tables repeat it.
  Tables are matched on the five header cells first, then the #ST one is
  preferred.
* empty / error state -- no table but the yellow banner (`flag-galben`)
  -> status 'empty'; neither -> 'error'; table present but no PT parsed
  -> 'parse_error'.
* "Zone afectate" -- rendered with `<br>` as line breaks and `<strong>` kept as
  a marker. A new PT starts at a line containing "Punct termic:"; its block
  count comes from "-- N blocuri/imobile". If a PT header carries no block
  count the PT is kept with `blocks_count` empty rather than dropped, instead
  of failing the whole parse.
* street lines -- split on the bullet and on `<br>`; the part before " - " is
  the street and the part after it is the buildings text, kept verbatim
  ("bl. A4, B3, D5"). Each street becomes its own row; the PT fields
  (name, block count, ...) are repeated on every street row of that PT, so
  `blocks_count` must not be summed over rows. A street listed twice under one
  PT gives one row with both buildings texts joined by "; ". Streets with no
  recognised type prefix are kept with `street_recognised` False, so drift in
  CMTEB's abbreviations is visible instead of silently dropping rows.
* a PT with no street lines still gives one row, with an empty street, so the
  PT is not lost.
* "Agentul termic afectat" -- one row per PT with boolean flags rather than a
  fan-out per service.
* "Data/ora estimarii" -- `dd.mm.yyyy HH:MM` -> ISO minute; "Nedefinit" or
  anything unparseable -> empty, with the raw text kept.
"""

import re
import unicodedata
from dataclasses import asdict, dataclass
from html.parser import HTMLParser

# Expected header cells of the outage table, in order (folded, substring match).
HEADER_TOKENS = ("sector", "zone", "agent", "cauza", "data")

PT_HEADER_RE = re.compile(r"punct\s+termic\s*:", re.I)
# Matches "-- 5 blocuri/imobile", "-- 12 blocuri", "– 3 imobile" and
# "-- -2 blocuri/imobile" (CMTEB published a negative count on 2026-02-06).
BLOCKS_RE = re.compile(
    r"(?:--?|–|—)\s*(-?\d+)\s*(blocuri/imobile|blocuri|imobile|bloc|imobil)\b", re.I
)
BULLET_RE = re.compile(r"[•·]|&bull;")
# Applied to diacritic-folded text.
SEVERITY_RE = re.compile(r"\b(oprire|deficient[ae]?)\b", re.I)
SERVICE_RE = re.compile(r"\b(acc|inc)\b", re.I)
REMEDIERE_RE = re.compile(r"(\d{1,2})\.(\d{1,2})\.(\d{4})\s+(\d{1,2}):(\d{2})")
# Street-type abbreviations CMTEB actually uses, matched on folded text.
STREET_TYPE_RE = re.compile(
    r"^(str|strada|bld|bd|b-?dul|bulevardul|cal|calea|sos|soseaua|int|intr|intrarea|"
    r"al|ale|alee|aleea|drm|drum|drumul|pta|piata|spl|splai|splaiul|prl|prel|prelungirea|"
    r"fdc|fundatura|fdt|pasaj|piateta|cart|cartier|complex)\b\.?\s*",
    re.I,
)

# Tags that never have a closing tag; they must not become the "current" node.
VOID_TAGS = {"br", "img", "input", "meta", "link", "hr", "col"}

CSV_COLUMNS = [
    "snapshot_ts_utc", "snapshot_ts_local", "raw_sha256", "row_index", "sector",
    "pt_name", "pt_name_norm", "blocks_count", "blocks_unit", "street", "street_recognised",
    "buildings", "zone_raw", "agent_raw", "is_oprire", "is_deficienta",
    "affects_acc", "affects_inc", "cause_raw", "remediere_raw", "remediere_iso",
]


# --- text helpers ------------------------------------------------------------
def fold(text):
    """Diacritic-fold, lowercase and collapse whitespace.

    This is the join key for PT names.

    Parameters
    ----------
    text : str or None
        Text to fold.

    Returns
    -------
    str
        Folded text; empty string for None.
    """
    decomposed = unicodedata.normalize("NFKD", text or "")
    kept = []
    for ch in decomposed:
        if not unicodedata.combining(ch):
            kept.append(ch)
    folded = "".join(kept)
    # The cedilla forms (ş, ţ) are not decomposed to their base letter by NFKD.
    folded = folded.replace("ş", "s").replace("ţ", "t").replace("Ş", "S").replace("Ţ", "T")
    return collapse(folded).lower()


def collapse(text):
    """Collapse runs of whitespace to one space and trim.

    Parameters
    ----------
    text : str or None
        Text to clean.

    Returns
    -------
    str
        Cleaned text; empty string for None.
    """
    return re.sub(r"\s+", " ", text or "").strip()


# --- minimal DOM -------------------------------------------------------------
class _Node:
    """One HTML element: tag, attributes, children (nodes or strings), parent."""

    __slots__ = ("tag", "attrs", "children", "parent")

    def __init__(self, tag, attrs, parent):
        """Create a node.

        Parameters
        ----------
        tag : str
            Lower-case tag name.
        attrs : dict
            Attribute name -> value.
        parent : _Node or None
            Enclosing node; None for the root.
        """
        self.tag = tag
        self.attrs = attrs
        self.children = []
        self.parent = parent

    def find_all(self, tag):
        """Return every descendant element with the given tag, in document order.

        Parameters
        ----------
        tag : str
            Tag name to look for.

        Returns
        -------
        list of _Node
            Matching descendants.
        """
        found = []
        for child in self.children:
            if isinstance(child, str):
                continue
            if child.tag == tag:
                found.append(child)
            found.extend(child.find_all(tag))
        return found

    def child_elements(self, tags):
        """Return the direct child elements whose tag is in `tags`.

        Parameters
        ----------
        tags : tuple of str
            Accepted tag names.

        Returns
        -------
        list of _Node
            Matching direct children.
        """
        found = []
        for child in self.children:
            if isinstance(child, _Node) and child.tag in tags:
                found.append(child)
        return found

    def text(self):
        """Return the concatenated text of this node and all descendants.

        Returns
        -------
        str
            Raw text, whitespace untouched.
        """
        parts = []
        for child in self.children:
            if isinstance(child, str):
                parts.append(child)
            else:
                parts.append(child.text())
        return "".join(parts)

    def has_ancestor(self, tag, attr, value):
        """Tell whether some ancestor has the given tag and attribute value.

        Parameters
        ----------
        tag : str
            Ancestor tag name.
        attr : str
            Attribute name.
        value : str
            Required attribute value.

        Returns
        -------
        bool
            True if such an ancestor exists.
        """
        node = self.parent
        while node is not None:
            if node.tag == tag and node.attrs.get(attr) == value:
                return True
            node = node.parent
        return False


def _attrs_to_dict(attrs):
    """Convert HTMLParser's attribute list to a dict, mapping None to "".

    Parameters
    ----------
    attrs : list of tuple
        (name, value) pairs; value is None for bare attributes.

    Returns
    -------
    dict
        Attribute name -> string value.
    """
    result = {}
    for name, value in attrs:
        result[name] = value or ""
    return result


class _TreeBuilder(HTMLParser):
    """Build a `_Node` tree; HTMLParser only offers callbacks, not a tree."""

    def __init__(self):
        """Start with an empty root node."""
        super().__init__(convert_charrefs=True)
        self.root = _Node("root", {}, None)
        self.cur = self.root

    def handle_starttag(self, tag, attrs):
        """Open an element (void elements are added but not entered)."""
        node = _Node(tag, _attrs_to_dict(attrs), self.cur)
        self.cur.children.append(node)
        if tag not in VOID_TAGS:
            self.cur = node

    def handle_startendtag(self, tag, attrs):
        """Add a self-closing element such as `<br/>`."""
        self.cur.children.append(_Node(tag, _attrs_to_dict(attrs), self.cur))

    def handle_endtag(self, tag):
        """Close the nearest open element with this tag.

        CMTEB's markup is not always well-formed, so unmatched end tags are
        ignored and missing ones are implicitly closed.
        """
        node = self.cur
        while node is not None and node.tag != tag:
            node = node.parent
        if node is not None and node.parent is not None:
            self.cur = node.parent

    def handle_data(self, data):
        """Append text to the current element."""
        self.cur.children.append(data)


def parse_html(html):
    """Parse an HTML string into a `_Node` tree.

    Parameters
    ----------
    html : str
        Page source.

    Returns
    -------
    _Node
        Synthetic root node.
    """
    builder = _TreeBuilder()
    builder.feed(html)
    return builder.root


# --- records -----------------------------------------------------------------
@dataclass
class Observation:
    """One street under one punct termic (PT) in one row of the outage table."""

    row_index: int                # 0-based table row; PTs of one row share cause/estimate
    sector: int | None
    pt_name: str                  # as printed (inside <strong>), trimmed
    pt_name_norm: str             # folded; "pt/ct/sc" prefix and "partial" NOT stripped
    blocks_count: int | None      # PT total, repeated on each street row; None if absent
    blocks_unit: str              # 'blocuri/imobile' | 'blocuri' | 'imobile' | ''
    street: str = ""              # as printed, e.g. 'Bld Basarabia'; '' if the PT has none
    street_recognised: bool = False  # street type prefix (Str, Bld, ...) was recognised
    buildings: str = ""           # text after " - ", e.g. 'bl. A4, B3, D5'
    zone_raw: str = ""            # PT header + all its street lines, in case the rules are wrong
    agent_raw: str = ""
    is_oprire: bool = False
    is_deficienta: bool = False
    affects_acc: bool = False
    affects_inc: bool = False
    cause_raw: str = ""
    remediere_raw: str = ""
    remediere_iso: str = ""       # 'YYYY-MM-DDTHH:MM' or ''

    def as_row(self):
        """Return the observation as a flat dict for CSV output.

        Returns
        -------
        dict
            Field name -> value.
        """
        return asdict(self)


@dataclass
class ParseResult:
    """Outcome of parsing one page."""

    status: str                   # ok | empty | error | parse_error
    observations: list[Observation]
    n_rows: int
    error: str | None = None


# --- "Zone afectate" cell ----------------------------------------------------
def _cell_tokens(node, tokens):
    """Flatten a cell into a token list: ("text", s), ("br", ""), ("strong", s).

    Parameters
    ----------
    node : _Node
        Element to walk.
    tokens : list of tuple
        Output list, appended to in place.

    Returns
    -------
    list of tuple
        The same `tokens` list.
    """
    for child in node.children:
        if isinstance(child, str):
            tokens.append(("text", child))
        elif child.tag == "br":
            tokens.append(("br", ""))
        elif child.tag in ("strong", "b"):
            tokens.append(("strong", child.text()))
        else:
            _cell_tokens(child, tokens)
    return tokens


def zone_lines(cell):
    """Split a "Zone afectate" cell into its `<br>`-separated lines.

    Parameters
    ----------
    cell : _Node
        The `<td>` element.

    Returns
    -------
    list of tuple
        (line_text, strong_text) per line; strong_text is None when the line
        has no `<strong>` (the PT name marker).
    """
    lines = []
    parts = []
    strong = None
    # A trailing "br" flushes the last line, so no special case after the loop.
    tokens = _cell_tokens(cell, []) + [("br", "")]
    for kind, value in tokens:
        if kind == "text":
            parts.append(value)
        elif kind == "strong":
            strong = collapse(value)
            parts.append(value)
        else:
            text = collapse("".join(parts))
            if text or strong is not None:
                lines.append((text, strong))
            parts = []
            strong = None
    return lines


def parse_street_line(line):
    """Split a line such as 'Şos Mihai Bravu - bl. P7' into street and buildings.

    Parameters
    ----------
    line : str
        One street line, possibly with a leading bullet.

    Returns
    -------
    tuple of (str, str, bool)
        The street ('Şos Mihai Bravu'), the buildings text ('bl. P7', "" when
        the line has no " - ") and whether the street's type prefix was
        recognised. ("", "", False) for a blank line.
    """
    cleaned = collapse(BULLET_RE.sub(" ", line)).strip(" -")
    if not cleaned:
        return "", "", False
    parts = cleaned.split(" - ", 1)
    street = parts[0].strip()
    buildings = ""
    if len(parts) == 2:
        buildings = parts[1].strip()
    recognised = STREET_TYPE_RE.match(fold(street)) is not None
    return street, buildings, recognised


def _pt_name_from_header(text, strong, blocks_match):
    """Return the PT name from a "Punct termic:" header line.

    Parameters
    ----------
    text : str
        The whole header line.
    strong : str or None
        Text of the `<strong>` element on the line, if any.
    blocks_match : re.Match or None
        BLOCKS_RE match on `text`, if any.

    Returns
    -------
    str
        The PT name, trimmed.
    """
    if strong:
        return collapse(strong)
    # No <strong>: take what sits between "Punct termic:" and the block count.
    after = PT_HEADER_RE.split(text, 1)[1]
    after_offset = len(text) - len(after)
    name = after
    if blocks_match is not None and blocks_match.start() >= after_offset:
        name = after[: blocks_match.start() - after_offset]
    return collapse(name.strip(" -–—"))


def _new_pt_entry(text, strong):
    """Start a PT entry from a header line.

    Parameters
    ----------
    text : str
        The header line.
    strong : str or None
        The `<strong>` text on the line.

    Returns
    -------
    dict
        Entry with pt_name, blocks_count, blocks_unit, header, street_lines.
    """
    match = BLOCKS_RE.search(text)
    blocks_count = None
    blocks_unit = ""
    if match is not None:
        blocks_count = int(match.group(1))
        blocks_unit = match.group(2).lower()
    return {
        "pt_name": _pt_name_from_header(text, strong, match),
        "blocks_count": blocks_count,
        "blocks_unit": blocks_unit,
        "header": text,
        "street_lines": [],
    }


def _merge_buildings(street, buildings):
    """Append the buildings text of a repeated street line to the first one.

    Parameters
    ----------
    street : dict
        Street dict built by `_distinct_streets`; updated in place.
    buildings : str
        Buildings text from the repeated line; ignored when empty.

    Returns
    -------
    None
    """
    if not buildings:
        return
    if street["buildings"]:
        street["buildings"] = street["buildings"] + "; " + buildings
    else:
        street["buildings"] = buildings


def _distinct_streets(street_lines):
    """Return the distinct streets under one PT, each with its buildings text.

    Parameters
    ----------
    street_lines : list of str
        Raw lines under the PT header; one line may hold several bullets.

    Returns
    -------
    list of dict
        One dict per street (street, buildings, recognised), in order of first
        appearance. Streets are deduplicated on the folded name; the buildings
        texts of repeated streets are joined by "; " so none are lost.
    """
    by_key = {}
    for raw_line in street_lines:
        for piece in BULLET_RE.split(raw_line):
            name, buildings, recognised = parse_street_line(piece)
            if not name:
                continue
            key = fold(name)
            if key not in by_key:
                by_key[key] = {"street": name, "buildings": buildings, "recognised": recognised}
                continue
            _merge_buildings(by_key[key], buildings)
    # dicts keep insertion order, so this is the order of first appearance.
    return list(by_key.values())


def _finish_pt_entry(entry):
    """Fill streets and zone_raw on a PT entry.

    Parameters
    ----------
    entry : dict
        Entry built by `_new_pt_entry`, with its street_lines collected.

    Returns
    -------
    dict
        The same entry, completed.
    """
    entry["streets"] = _distinct_streets(entry["street_lines"])
    zone_parts = [entry["header"]]
    for line in entry["street_lines"]:
        zone_parts.append("• " + collapse(BULLET_RE.sub(" ", line)))
    entry["zone_raw"] = " ".join(zone_parts)
    return entry


def parse_zone_cell(cell):
    """Split a "Zone afectate" cell into one dict per punct termic.

    Parameters
    ----------
    cell : _Node
        The `<td>` element.

    Returns
    -------
    list of dict
        One completed entry per PT (see `_finish_pt_entry`).
    """
    entries = []
    current = None
    for text, strong in zone_lines(cell):
        if PT_HEADER_RE.search(text):
            if current is not None:
                entries.append(_finish_pt_entry(current))
            current = _new_pt_entry(text, strong)
            continue
        if current is None:
            # Street lines before any PT header occur in early pages: keep
            # them under a nameless PT rather than dropping them.
            current = {"pt_name": "", "blocks_count": None, "blocks_unit": "",
                       "header": "", "street_lines": []}
        if text:
            current["street_lines"].append(text)
    if current is not None:
        entries.append(_finish_pt_entry(current))
    return entries


# --- other cells -------------------------------------------------------------
def parse_agent(raw):
    """Turn "Agentul termic afectat" into severity and service flags.

    A combined value such as "Oprire ACC/INC" sets both service flags on one
    row instead of fanning out into two rows.

    Parameters
    ----------
    raw : str
        Cell text, e.g. "Deficienţă INC".

    Returns
    -------
    dict
        is_oprire, is_deficienta, affects_acc, affects_inc (all bool).
    """
    folded = fold(raw)
    severities = SEVERITY_RE.findall(folded)
    services = SERVICE_RE.findall(folded)
    is_oprire = False
    is_deficienta = False
    for severity in severities:
        if severity.startswith("oprire"):
            is_oprire = True
        if severity.startswith("deficient"):
            is_deficienta = True
    return {
        "is_oprire": is_oprire,
        "is_deficienta": is_deficienta,
        "affects_acc": "acc" in services,
        "affects_inc": "inc" in services,
    }


def parse_remediere(raw):
    """Convert the estimated restore time "dd.mm.yyyy HH:MM" to ISO.

    Parameters
    ----------
    raw : str or None
        Cell text, e.g. "19.12.2021 23:00" or "Nedefinit".

    Returns
    -------
    str
        'YYYY-MM-DDTHH:MM', or "" when no date is found.
    """
    match = REMEDIERE_RE.search(raw or "")
    if match is None:
        return ""
    day = int(match.group(1))
    month = int(match.group(2))
    year = int(match.group(3))
    hour = int(match.group(4))
    minute = int(match.group(5))
    return f"{year:04d}-{month:02d}-{day:02d}T{hour:02d}:{minute:02d}"


def parse_sector(raw):
    """Return the sector number, or None when the cell is not an integer.

    Parameters
    ----------
    raw : str
        Cell text.

    Returns
    -------
    int or None
        Sector number.
    """
    text = collapse(raw)
    if text.lstrip("-").isdigit():
        return int(text)
    return None


# --- page --------------------------------------------------------------------
def _header_ok(table):
    """Tell whether a table's first row has the five expected header cells.

    Parameters
    ----------
    table : _Node
        A `<table>` element.

    Returns
    -------
    bool
        True if the header signature matches.
    """
    rows = table.find_all("tr")
    if not rows:
        return False
    cells = rows[0].child_elements(("th", "td"))
    if len(cells) != len(HEADER_TOKENS):
        return False
    for token, cell in zip(HEADER_TOKENS, cells):
        if token not in fold(cell.text()):
            return False
    return True


def select_table(root):
    """Pick the "Toate sectoarele" outage table.

    Parameters
    ----------
    root : _Node
        Parsed page.

    Returns
    -------
    _Node or None
        The table, or None if the page has none.
    """
    all_tables = root.find_all("table")
    candidates = [table for table in all_tables if _header_ok(table)]
    if not candidates:
        candidates = [table for table in all_tables if "raport" in table.attrs.get("class", "")]
    if not candidates:
        return None
    # The all-sectors tab (div#ST) is the union of the six per-sector tables.
    for table in candidates:
        if table.has_ancestor("div", "id", "ST"):
            return table
    return candidates[0]


def _result_without_table(root):
    """Classify a page that has no outage table as 'empty' or 'error'.

    Parameters
    ----------
    root : _Node
        Parsed page.

    Returns
    -------
    ParseResult
        'empty' when CMTEB's yellow "no records" banner is present (a real
        zero), otherwise 'error' (backend failure page).
    """
    has_banner = "nu exista inregistrari" in fold(root.text())
    for div in root.find_all("div"):
        if "flag-galben" in div.attrs.get("class", ""):
            has_banner = True
    if has_banner:
        return ParseResult("empty", [], 0)
    return ParseResult("error", [], 0, "no outage table and no empty-state banner")


def _row_fields(cells, row_index):
    """Read the fields shared by every observation of one table row.

    Parameters
    ----------
    cells : list of _Node
        The row's `<td>` elements (at least five).
    row_index : int
        0-based index of the data row.

    Returns
    -------
    dict
        Observation keyword arguments that do not depend on the PT or street.
    """
    agent_raw = collapse(cells[2].text())
    remediere_raw = collapse(cells[4].text())
    fields = {
        "row_index": row_index,
        "sector": parse_sector(cells[0].text()),
        "agent_raw": agent_raw,
        "cause_raw": collapse(cells[3].text()),
        "remediere_raw": remediere_raw,
        "remediere_iso": parse_remediere(remediere_raw),
    }
    fields.update(parse_agent(agent_raw))
    return fields


def _pt_observations(entry, row_fields):
    """Turn one PT entry into one Observation per street.

    Parameters
    ----------
    entry : dict
        Completed PT entry (see `_finish_pt_entry`).
    row_fields : dict
        Fields shared by the whole table row (see `_row_fields`).

    Returns
    -------
    list of Observation
        One per street; a single street-less one if the PT lists no streets,
        so the PT itself is never dropped.
    """
    streets = entry["streets"]
    if not streets:
        streets = [{"street": "", "buildings": "", "recognised": False}]
    observations = []
    for street in streets:
        observation = Observation(
            pt_name=entry["pt_name"],
            pt_name_norm=fold(entry["pt_name"]),
            blocks_count=entry["blocks_count"],
            blocks_unit=entry["blocks_unit"],
            street=street["street"],
            street_recognised=street["recognised"],
            buildings=street["buildings"],
            zone_raw=entry["zone_raw"],
            **row_fields,
        )
        observations.append(observation)
    return observations


def parse_row(cells, row_index):
    """Parse one table row into one Observation per (punct termic, street).

    Parameters
    ----------
    cells : list of _Node
        The row's `<td>` elements (at least five).
    row_index : int
        0-based index of the data row.

    Returns
    -------
    list of Observation
        Observations for this row; empty if no PT could be read.
    """
    row_fields = _row_fields(cells, row_index)
    observations = []
    for entry in parse_zone_cell(cells[1]):
        observations.extend(_pt_observations(entry, row_fields))
    return observations


def parse_page(html):
    """Parse the whole CMTEB page.

    Parameters
    ----------
    html : str
        Page source.

    Returns
    -------
    ParseResult
        Status ('ok', 'empty', 'error', 'parse_error'), observations and the
        number of data rows in the table.
    """
    root = parse_html(html)
    table = select_table(root)
    if table is None:
        return _result_without_table(root)

    observations = []
    n_rows = 0
    for row in table.find_all("tr"):
        cells = row.child_elements(("td",))
        if len(cells) < 5:
            continue  # header row or malformed row
        observations.extend(parse_row(cells, n_rows))
        n_rows += 1

    if n_rows and not observations:
        return ParseResult("parse_error", [], n_rows, "table has rows but no PT could be parsed")
    if not observations:
        return ParseResult("empty", [], n_rows)
    return ParseResult("ok", observations, n_rows)
