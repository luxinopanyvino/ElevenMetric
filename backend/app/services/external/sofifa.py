"""SoFIFA reader — EA Sports FC club squads.

**What this source is.** SoFIFA republishes the player ratings from the EA
Sports FC video game (currently EA FC 26). Those ratings are an *opinion
published by a games studio*, not a measurement of the player. Everything this
module produces is stamped with that fact through
:func:`app.services.external.base.provenance`, and the UI repeats it wherever
the numbers drive a recommendation. The product is analysing the source's view
of a squad, and says so.

**Why it is hand-written.** There is no official API and no maintained PyPI
wrapper (``pip install sofifa`` does not resolve). The only maintained library
that reads SoFIFA pulls a browser-automation stack, which is disproportionate
for reading a static HTML table into a project whose constraint is to stay
light. So: ``urllib`` plus ``html.parser``, and no new dependency.

**How it survives the site changing.** Parsing is driven by the table's own
header labels rather than by fixed column indices, so a reordered or extended
table still reads correctly. When the markup moves far enough that the headers
cannot be found, the parser raises :class:`FetchError` naming what it expected —
it never returns a half-read squad. Two routes exist that do not depend on the
markup at all: :func:`load_squad_from_bytes` reads a saved page *or* a SoFIFA
export CSV, and is the path the tests use.

Run ``python -m app.services.external.sofifa --probe <url>`` to see exactly what
the parser found and what it missed on a live page.
"""

from __future__ import annotations

import csv
import io
import re
from datetime import date, datetime
from html.parser import HTMLParser
from urllib.parse import quote_plus

from app.services.external import http
from app.services.external.base import (
    FetchError,
    SourceCapability,
    SourcePlayer,
    SourceSquad,
    provenance,
)
from app.services.external.sofifa_map import (
    map_attributes,
    map_positions,
    normalise,
    parse_work_rate,
)

SOURCE = "sofifa"
BASE_URL = "https://sofifa.com"
#: The game edition SoFIFA currently publishes. Recorded in every provenance
#: record so a squad imported today is still identifiable in two editions' time.
EDITION = "EA FC 26"
PROVENANCE_NOTE = (
    "Player ratings published by SoFIFA from the EA Sports FC video game. "
    "These are a games studio's opinion, not measurements of the players."
)


def capability() -> SourceCapability:
    """What this source is and whether it can be used right now."""
    enabled, reason, remedy = http.fetch_enabled()
    return SourceCapability(
        key=SOURCE,
        label=f"SoFIFA — {EDITION} squads",
        what_it_is=(
            "Club squads and player ratings from the EA Sports FC video game, "
            "republished by sofifa.com. Real clubs and real player names, with "
            "attributes that are a games studio's judgement rather than "
            "measurements taken on a pitch."
        ),
        tier=1,
        supplies=["clubs", "squad lists", "positions", "overall and potential "
                  "ratings", "the full 0-99 attribute profile", "age", "foot",
                  "height and weight", "market value and wage"],
        does_not_supply=["minutes played", "fitness or fatigue", "season "
                         "statistics", "match events", "tracking"],
        available=True,   # the file route always works; see `reason` for live fetch
        reason="" if enabled else f"live fetching unavailable: {reason}",
        remedy="" if enabled else remedy,
        attribution="Data from sofifa.com (EA Sports FC ratings).",
        terms_url="https://sofifa.com/help/terms",
    )


# --- HTML table reader -----------------------------------------------------

class _Cell:
    """One table cell: its text, its links, and its tagged spans."""

    def __init__(self) -> None:
        self.text_parts: list[str] = []
        self.links: list[tuple[str, str]] = []       # (href, text)
        self.spans: list[tuple[str, str]] = []       # (class, text)

    @property
    def text(self) -> str:
        return " ".join(" ".join(self.text_parts).split())


class _TableParser(HTMLParser):
    """Pull every ``<table>`` into header labels plus structured rows.

    Deliberately generic. SoFIFA lets a visitor choose which columns a squad
    table shows, so reading by header label is the only stable approach — index
    3 is age today and value tomorrow.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tables: list[dict] = []
        self._table: dict | None = None
        self._row: list[_Cell] | None = None
        self._cell: _Cell | None = None
        self._in_header_cell = False
        self._link_depth = 0
        self._span_stack: list[tuple[str, int]] = []

    # -- structure --
    def handle_starttag(self, tag: str, attrs_list: list[tuple[str, str | None]]) -> None:
        attrs = {k: (v or "") for k, v in attrs_list}
        if tag == "table":
            self._table = {"headers": [], "rows": []}
        elif tag == "tr" and self._table is not None:
            self._row = []
        elif tag in ("td", "th") and self._row is not None:
            self._cell = _Cell()
            self._in_header_cell = tag == "th"
        elif tag == "a" and self._cell is not None:
            self._cell.links.append((attrs.get("href", ""), ""))
            self._link_depth += 1
        elif tag == "span" and self._cell is not None:
            self._cell.spans.append((attrs.get("class", ""), ""))
            self._span_stack.append((attrs.get("class", ""), len(self._cell.spans) - 1))

    def handle_endtag(self, tag: str) -> None:
        if tag == "table" and self._table is not None:
            self.tables.append(self._table)
            self._table = None
        elif tag == "tr" and self._row is not None and self._table is not None:
            if self._row:
                if self._in_row_is_header():
                    self._table["headers"] = [c.text for c in self._row]
                else:
                    self._table["rows"].append(self._row)
            self._row = None
            self._header_row = False
        elif tag in ("td", "th") and self._cell is not None and self._row is not None:
            if tag == "th":
                self._header_row = True
            self._row.append(self._cell)
            self._cell = None
            self._in_header_cell = False
        elif tag == "a" and self._link_depth:
            self._link_depth -= 1
        elif tag == "span" and self._span_stack:
            self._span_stack.pop()

    _header_row = False

    def _in_row_is_header(self) -> bool:
        return self._header_row

    # -- text --
    def handle_data(self, data: str) -> None:
        if self._cell is None or not data.strip():
            return
        self._cell.text_parts.append(data)
        if self._link_depth and self._cell.links:
            href, text = self._cell.links[-1]
            self._cell.links[-1] = (href, (text + " " + data).strip())
        if self._span_stack:
            _, index = self._span_stack[-1]
            cls, text = self._cell.spans[index]
            self._cell.spans[index] = (cls, (text + " " + data).strip())


def _tables(html: str) -> list[dict]:
    parser = _TableParser()
    parser.feed(html)
    parser.close()
    return parser.tables


# --- Value helpers ---------------------------------------------------------

_MONEY = re.compile(r"([\d.,]+)\s*([KMB]?)", re.IGNORECASE)
_MULTIPLIER = {"": 1, "K": 1_000, "M": 1_000_000, "B": 1_000_000_000}
_PLAYER_HREF = re.compile(r"/player/(\d+)")
_TEAM_HREF = re.compile(r"/team/(\d+)")


def _money(text: str) -> int | None:
    """``"€75.5M"`` → ``75_500_000``. Returns ``None`` for a blank or a dash."""
    cleaned = text.replace("€", "").replace("$", "").replace("£", "").strip()
    if not cleaned or cleaned in {"-", "—", "0"}:
        return None
    if (match := _MONEY.search(cleaned)) is None:
        return None
    number, suffix = match.group(1), match.group(2).upper()
    try:
        value = float(number.replace(",", ""))
    except ValueError:
        return None
    return int(value * _MULTIPLIER.get(suffix, 1))


def _int(text: str) -> int | None:
    if (match := re.search(r"-?\d+", text or "")) is None:
        return None
    return int(match.group())


def _float(text: str) -> float | None:
    value = _int(text)
    return None if value is None else float(value)


def _date(text: str) -> date | None:
    for fmt in ("%Y-%m-%d", "%b %d, %Y", "%d %b %Y", "%Y"):
        try:
            parsed = datetime.strptime(text.strip(), fmt)
        except ValueError:
            continue
        return parsed.date()
    return None


#: Header label (normalised) → the field it fills. SoFIFA's own column names,
#: plus the spellings its export CSVs use.
_HEADER_FIELDS: dict[str, str] = {
    "name": "name", "shortname": "name", "longname": "full_name",
    "player": "name", "": "",
    "age": "age", "dob": "birth_date", "birthdate": "birth_date",
    "ovr": "overall", "overall": "overall", "overallrating": "overall",
    "pot": "potential", "potential": "potential",
    "value": "market_value_eur", "valueeur": "market_value_eur",
    "wage": "wage_eur_per_year", "wageeur": "wage_eur_per_year",
    "height": "height_cm", "heightcm": "height_cm",
    "weight": "weight_kg", "weightkg": "weight_kg",
    "foot": "preferred_foot", "preferredfoot": "preferred_foot",
    "nationality": "nationality", "nationalityname": "nationality",
    "team": "club", "club": "club", "clubname": "club",
    "league": "league", "leaguename": "league",
    "position": "position_raw", "positions": "position_raw",
    "playerpositions": "position_raw", "bp": "position_raw",
    "bestposition": "position_raw",
    "contract": "contract_until", "contractvaliduntil": "contract_until",
    "workrate": "work_rate", "wr": "work_rate",
    "sofifaid": "source_id", "playerid": "source_id", "id": "source_id",
    "shirtnumber": "shirt_number", "clubjerseynumber": "shirt_number",
    "jerseynumber": "shirt_number", "number": "shirt_number",
}


# --- Team page -------------------------------------------------------------

def parse_team_page(html: str, *, url: str, retrieved: str = "fetch",
                    retrieved_at: str | None = None) -> SourceSquad:
    """Read a SoFIFA club page into a :class:`SourceSquad`.

    Raises :class:`FetchError` — naming what was expected — rather than
    returning a partial squad, because a squad that silently lost half its
    players is the single worst outcome this parser could produce.
    """
    tables = [t for t in _tables(html) if t["rows"]]
    squad_table = None
    for table in tables:
        if any(_PLAYER_HREF.search(href)
               for row in table["rows"] for cell in row for href, _ in cell.links):
            squad_table = table
            break

    if squad_table is None:
        raise FetchError(
            SOURCE, url,
            "a squad table whose rows link to /player/<id>",
            f"{len(tables)} table(s) found, none containing player links — "
            "sofifa.com's markup has probably changed; run with --probe",
        )

    fields = [_HEADER_FIELDS.get(normalise(h), "") for h in squad_table["headers"]]
    club_name, league, country = _team_identity(html, url)

    players: list[SourcePlayer] = []
    for row in squad_table["rows"]:
        player = _player_from_row(row, fields, squad_table["headers"])
        if player is not None:
            players.append(player)

    if not players:
        raise FetchError(
            SOURCE, url, "at least one player row",
            f"the squad table had {len(squad_table['rows'])} row(s) but none "
            "carried a readable player",
        )

    source_id = (match.group(1) if (match := _TEAM_HREF.search(url)) else url)
    record = provenance(source=SOURCE, edition=EDITION, source_id=source_id,
                        source_url=url, retrieved=retrieved, note=PROVENANCE_NOTE)
    if retrieved_at:
        record["retrieved_at"] = retrieved_at

    return SourceSquad(source_id=source_id, name=club_name, league=league,
                       country=country, players=players, provenance=record)


_TITLE = re.compile(r"<title>(.*?)</title>", re.IGNORECASE | re.DOTALL)
_H1 = re.compile(r"<h1[^>]*>(.*?)</h1>", re.IGNORECASE | re.DOTALL)
_TAGS = re.compile(r"<[^>]+>")


def _team_identity(html: str, url: str) -> tuple[str, str | None, str | None]:
    """Club name, league and country, from whichever of them the page carries."""
    name = ""
    if (match := _H1.search(html)) is not None:
        name = _TAGS.sub(" ", match.group(1)).strip()
    if not name and (match := _TITLE.search(html)) is not None:
        # "Real Madrid - EA FC 26 - sofifa" → "Real Madrid"
        name = _TAGS.sub(" ", match.group(1)).split("-")[0].strip()
    name = " ".join(name.split()) or f"SoFIFA team {url}"

    league = country = None
    if (match := re.search(r'href="/league/\d+[^"]*"[^>]*>([^<]+)<', html)) is not None:
        league = " ".join(match.group(1).split())
    if (match := re.search(r'title="([^"]+)"[^>]*class="[^"]*flag', html)) is not None:
        country = " ".join(match.group(1).split())
    return name, league, country


def _player_from_row(row: list[_Cell], fields: list[str],
                     headers: list[str]) -> SourcePlayer | None:
    """One squad-table row → a :class:`SourcePlayer`, or ``None`` if unreadable."""
    source_id = ""
    name = ""
    positions: list[str] = []
    for cell in row:
        for href, text in cell.links:
            if (match := _PLAYER_HREF.search(href)) is not None:
                source_id = source_id or match.group(1)
                if text and not name:
                    name = text
        for cls, text in cell.spans:
            if "pos" in cls and text:
                positions.append(text)
    if not source_id:
        return None

    raw: dict[str, str] = {}
    attributes_raw: dict[str, float] = {}
    for index, cell in enumerate(row):
        if index >= len(fields):
            break
        if field := fields[index]:
            raw[field] = cell.text
            continue
        # A header this module has no field for is an attribute column when its
        # value is a bare 0-99 number — that is how SoFIFA renders the optional
        # attribute columns a visitor can switch on. Key it by its own header
        # label so `map_attributes` can resolve it (and report it by name if it
        # cannot); keying by column index would guarantee it never maps.
        label = headers[index] if index < len(headers) else ""
        if label and (value := _float(cell.text)) is not None and 0 <= value <= 99:
            attributes_raw[label] = value

    player = SourcePlayer(
        source_id=source_id,
        name=name or raw.get("name", "") or f"SoFIFA player {source_id}",
        position_raw=" ".join(positions) or raw.get("position_raw", ""),
        shirt_number=_int(raw.get("shirt_number", "")),
        age=_int(raw.get("age", "")),
        birth_date=_date(raw.get("birth_date", "")),
        nationality=raw.get("nationality") or None,
        overall=_float(raw.get("overall", "")),
        potential=_float(raw.get("potential", "")),
        preferred_foot=(raw.get("preferred_foot") or "").lower() or None,
        height_cm=_int(raw.get("height_cm", "")),
        weight_kg=_int(raw.get("weight_kg", "")),
        market_value_eur=_money(raw.get("market_value_eur", "")),
        wage_eur_per_year=_money(raw.get("wage_eur_per_year", "")),
        contract_until=_date(raw.get("contract_until", "")),
        attributes_raw=attributes_raw,
    )
    if work_rate := raw.get("work_rate"):
        player.attributes_raw.update(parse_work_rate(work_rate))
    return player


# --- Player page -----------------------------------------------------------

_ATTR_PAIR = re.compile(
    r"<(?:span|em|div)[^>]*>\s*(\d{1,2})\s*</(?:span|em|div)>\s*(?:<[^>]+>\s*)*"
    r"([A-Za-z][A-Za-z \-]{2,30}?)\s*<",
)


def parse_player_page(html: str) -> dict[str, float]:
    """Pull the 0-99 attribute grid off a SoFIFA player page.

    Returns source labels → values, unmapped. An empty result means the grid was
    not recognised; callers treat that as "no attributes found for this player"
    and say so in the preview rather than inventing any.
    """
    found: dict[str, float] = {}
    for value, label in _ATTR_PAIR.findall(html):
        number = float(value)
        if 0 <= number <= 99:
            found.setdefault(" ".join(label.split()), number)
    return found


# --- Live fetch ------------------------------------------------------------

def search_clubs(query: str, *, limit: int = 20) -> list[dict]:
    """Clubs whose name matches ``query``. Reads nothing into the database."""
    url = f"{BASE_URL}/teams?keyword={quote_plus(query)}"
    response = http.fetch(url, source=SOURCE, expected="a club search results table")

    out: list[dict] = []
    seen: set[str] = set()
    for table in _tables(response.body):
        for row in table["rows"]:
            for cell in row:
                for href, text in cell.links:
                    match = _TEAM_HREF.search(href)
                    if match is None or not text.strip():
                        continue
                    club_id = match.group(1)
                    if club_id in seen:
                        continue
                    seen.add(club_id)
                    out.append({
                        "source_id": club_id,
                        "name": " ".join(text.split()),
                        "url": f"{BASE_URL}/team/{club_id}",
                        "row": [c.text for c in row],
                    })
    if not out:
        raise FetchError(SOURCE, url, "at least one link to /team/<id>",
                         "no clubs matched, or the results markup has changed")
    return out[:limit]


def fetch_squad(club_id: str, *, with_attributes: bool = True,
                max_players: int = 40) -> SourceSquad:
    """Fetch a club's squad, optionally with each player's full attribute grid.

    ``with_attributes`` costs one throttled request per player, so it is capped
    and cached. Without it the squad still carries overall and potential — the
    engines work, positional fit is simply coarser.
    """
    url = f"{BASE_URL}/team/{club_id}"
    response = http.fetch(url, source=SOURCE, expected="a club squad table")
    squad = parse_team_page(response.body, url=url,
                            retrieved="cache" if response.from_cache else "fetch",
                            retrieved_at=response.retrieved_at)

    if with_attributes:
        for player in squad.players[:max_players]:
            player_url = f"{BASE_URL}/player/{player.source_id}"
            try:
                page = http.fetch(player_url, source=SOURCE,
                                  expected="a player attribute grid")
            except FetchError:
                # One unreadable player page must not lose the other twenty-four.
                # The preview reports the player as having no attributes.
                continue
            player.attributes_raw.update(parse_player_page(page.body))

    return squad


# --- File route (no network, no markup dependency) -------------------------

def load_squad_from_bytes(raw: bytes, *, filename: str = "",
                          club_name: str = "") -> SourceSquad:
    """Read a squad from a file the user supplies.

    Two accepted shapes:

    * a **saved SoFIFA club page** (``.html``) — parsed exactly as a live fetch
      would parse it;
    * a **SoFIFA-format export** (``.csv``) — the column vocabulary the widely
      circulated EA FC player datasets use (``sofifa_id``, ``short_name``,
      ``player_positions``, ``attacking_finishing``…).

    This route is what makes the feature usable with no network and makes its
    tests deterministic, and it is why a change to sofifa.com's markup degrades
    the feature rather than removing it.
    """
    text = raw.decode("utf-8", errors="replace")
    lowered = filename.lower()

    if lowered.endswith(".html") or lowered.endswith(".htm") or "<html" in text[:2000].lower():
        squad = parse_team_page(text, url=filename or "(uploaded file)",
                                retrieved="file")
        if club_name:
            squad.name = club_name
        return squad

    return _squad_from_csv(text, filename=filename, club_name=club_name)


def _squad_from_csv(text: str, *, filename: str, club_name: str) -> SourceSquad:
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    if not reader.fieldnames:
        raise FetchError(SOURCE, filename or "(uploaded file)",
                         "a header row", "the file has no columns")

    field_for = {name: _HEADER_FIELDS.get(normalise(name), "")
                 for name in reader.fieldnames}

    players: list[SourcePlayer] = []
    league = country = None
    club = club_name
    for index, row in enumerate(reader, start=2):
        raw_fields: dict[str, str] = {}
        attributes_raw: dict[str, float] = {}
        for column, value in row.items():
            if column is None or value is None:
                continue
            field = field_for.get(column, "")
            if field:
                raw_fields[field] = value.strip()
            else:
                # Anything not a known field is a candidate attribute column;
                # `map_attributes` decides, and reports what it could not place.
                if (number := _float(value)) is not None:
                    attributes_raw[column] = number

        # Exports carry both `long_name` and `short_name`. The full name is the
        # identity; the short one is what goes on a shirt and on the pitch.
        full_name = (raw_fields.get("full_name") or "").strip()
        short_name = (raw_fields.get("name") or "").strip()
        name = full_name or short_name
        if not name:
            continue
        club = club or raw_fields.get("club") or ""
        league = league or raw_fields.get("league") or None
        country = country or raw_fields.get("nationality") or None

        player = SourcePlayer(
            source_id=raw_fields.get("source_id") or f"row{index}",
            name=name,
            known_as=short_name if full_name and short_name != full_name else "",
            position_raw=raw_fields.get("position_raw", ""),
            shirt_number=_int(raw_fields.get("shirt_number", "")),
            age=_int(raw_fields.get("age", "")),
            birth_date=_date(raw_fields.get("birth_date", "")),
            nationality=raw_fields.get("nationality") or None,
            overall=_float(raw_fields.get("overall", "")),
            potential=_float(raw_fields.get("potential", "")),
            attributes_raw=attributes_raw,
            preferred_foot=(raw_fields.get("preferred_foot") or "").lower() or None,
            height_cm=_int(raw_fields.get("height_cm", "")),
            weight_kg=_int(raw_fields.get("weight_kg", "")),
            market_value_eur=_money(raw_fields.get("market_value_eur", "")),
            wage_eur_per_year=_money(raw_fields.get("wage_eur_per_year", "")),
            contract_until=_date(raw_fields.get("contract_until", "")),
        )
        if work_rate := raw_fields.get("work_rate"):
            player.attributes_raw.update(parse_work_rate(work_rate))
        players.append(player)

    if not players:
        raise FetchError(SOURCE, filename or "(uploaded file)",
                         "at least one row with a player name",
                         f"read {len(field_for)} column(s), no usable rows")

    return SourceSquad(
        source_id=filename or "upload",
        name=club or "Imported squad",
        league=league,
        country=country,
        players=players,
        provenance=provenance(source=SOURCE, edition=EDITION,
                              source_id=filename or "upload",
                              source_url=None, retrieved="file",
                              note=PROVENANCE_NOTE),
    )


# --- Probe -----------------------------------------------------------------

def _probe(url: str) -> int:
    """Print what the parser found on ``url``. Used to confirm selectors.

    The point of this command is that a markup change is diagnosable in one
    step: it prints the tables it saw, the headers it matched, and the first
    players it read, so a fix is a table edit rather than an investigation.
    """
    from app.services.external.sofifa_map import map_attributes as _map

    response = http.fetch(url, source=SOURCE, expected="a SoFIFA page",
                          ttl_hours=0)
    tables = _tables(response.body)
    print(f"URL          : {url}")
    print(f"Fetched at   : {response.retrieved_at} "
          f"({'cache' if response.from_cache else 'live'})")
    print(f"Bytes        : {len(response.body)}")
    print(f"Tables found : {len(tables)}")
    for index, table in enumerate(tables):
        print(f"  [{index}] headers={table['headers']!r} rows={len(table['rows'])}")

    try:
        squad = parse_team_page(response.body, url=url)
    except FetchError as exc:
        print(f"\nPARSE FAILED: {exc}")
        grid = parse_player_page(response.body)
        if grid:
            mapped, unmapped = _map(grid)
            print(f"\nLooks like a player page. {len(grid)} attribute(s) found, "
                  f"{len(mapped)} mapped, {len(unmapped)} unmapped.")
            print(f"  mapped   : {sorted(mapped)}")
            print(f"  unmapped : {[u['source_label'] for u in unmapped]}")
        return 1

    print(f"\nClub    : {squad.name}  (league={squad.league}, country={squad.country})")
    print(f"Players : {len(squad.players)}")
    for player in squad.players[:5]:
        print(f"  {player.source_id:>8}  {player.name:<28} "
              f"pos={player.position_raw:<10} ovr={player.overall} "
              f"pot={player.potential} age={player.age} "
              f"value={player.market_value_eur}")
    missing = [p.name for p in squad.players if p.overall is None]
    if missing:
        print(f"\nNo overall rating read for {len(missing)}: {missing[:5]}")
    return 0


if __name__ == "__main__":   # pragma: no cover - developer tool
    import argparse
    import sys

    parser = argparse.ArgumentParser(
        description="Probe a SoFIFA page and report what the parser reads.")
    parser.add_argument("--probe", metavar="URL", required=True,
                        help="e.g. https://sofifa.com/team/241")
    args = parser.parse_args()
    sys.exit(_probe(args.probe))
