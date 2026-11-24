"""
Pure parsers, filters, and URL builders for e-IC 2.0 GRM seatmap payloads and search URLs.

Extracted from ebilet_seat_scraper.py so unit tests, main API handlers, and the Playwright
scanner can share deterministic logic without coupling to Playwright internals.
"""
import re
import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Optional, Tuple

import bs4

TARGET_CLASS = 2  # Hardcoded default: 2nd class sitting carriages

TAG_BIKE = "BIKE"
TAG_PREGNANT = "PREGNANT"
TAG_CHILD = "CHILD"
TAG_ELDERLY = "ELDERLY"
TAG_PRM_ASSIST = "PRM_ASSIST"
TAG_WHEELCHAIR = "WHEELCHAIR"
TAG_QUIET = "QUIET"
TAG_WINDOW = "WINDOW"
TAG_AISLE = "AISLE"
TAG_TABLE = "TABLE"

RESTRICTED_TAGS = {
    TAG_BIKE,
    TAG_PREGNANT,
    TAG_CHILD,
    TAG_ELDERLY,
    TAG_PRM_ASSIST,
    TAG_WHEELCHAIR,
}

TAG_LABELS_PL = {
    TAG_BIKE: "Rower",
    TAG_PREGNANT: "Kobieta w ciąży",
    TAG_CHILD: "Z dzieckiem",
    TAG_ELDERLY: "Senior",
    TAG_PRM_ASSIST: "Niepełnosprawność",
    TAG_WHEELCHAIR: "Osoba na wózku",
    TAG_QUIET: "Strefa ciszy",
    TAG_WINDOW: "Okno",
    TAG_AISLE: "Korytarz",
    TAG_TABLE: "Stolik",
}

PRM_WHEELCHAIR_MARKERS = (
    "na wózku",
    "na wozku",
    "wózku inwalidzkim",
    "wozku inwalidzkim",
    "opiekuna osoby na wózku",
    "opiekuna osoby na wozku",
    "wózek inwalidzki",
    "wozek inwalidzki",
)

SOLD_OUT_MARKERS = (
    "brak_miejsc",
    "brak miejsc",
    "brak wolnych miejsc",
    "brak dostępnych wagonów",
    "wyprzedan",
    "wyczerpano pulę miejsc",
)


def is_prm_wheelchair_label(aria_label: str) -> bool:
    """Returns True if an aria-label describes a wheelchair-only or wheelchair-companion PRM seat."""
    low = (aria_label or "").lower()
    return any(marker in low for marker in PRM_WHEELCHAIR_MARKERS)


def is_restricted_tag(tag: str) -> bool:
    return tag in RESTRICTED_TAGS


def format_seat_tag_pl(tag: str) -> str:
    return TAG_LABELS_PL.get(tag, tag)


def seat_priority_tier(tags: List[str]) -> int:
    """
    Hierarchy:
    0 = Normal / Quiet zone / comfort-only
    1 = Bike
    2 = Pregnant / Child / Elderly / PRM without wheelchair
    3 = Wheelchair
    """
    if TAG_WHEELCHAIR in tags:
        return 3
    if any(t in tags for t in (TAG_PREGNANT, TAG_CHILD, TAG_ELDERLY, TAG_PRM_ASSIST)):
        return 2
    if TAG_BIKE in tags:
        return 1
    return 0


def seat_tier_penalty(tags: List[str]) -> float:
    tier = seat_priority_tier(tags)
    if tier == 1:
        return 3.0
    if tier == 2:
        return 8.0
    if tier == 3:
        return 15.0
    return 0.0


def is_seat_allowed(tags: List[str], prefs: Any) -> bool:
    if prefs is None:
        allow_bike = True
        allow_pregnant = allow_child = allow_elderly = allow_prm_assist = allow_wheelchair = False
    else:
        allow_bike = bool(getattr(prefs, "allow_bike", True))
        allow_pregnant = bool(getattr(prefs, "allow_pregnant", False))
        allow_child = bool(getattr(prefs, "allow_child", False))
        allow_elderly = bool(getattr(prefs, "allow_elderly", False))
        allow_prm_assist = bool(getattr(prefs, "allow_prm_assist", False))
        allow_wheelchair = bool(getattr(prefs, "allow_wheelchair", False))

    if TAG_WHEELCHAIR in tags and not allow_wheelchair:
        return False
    if TAG_PREGNANT in tags and not allow_pregnant:
        return False
    if TAG_CHILD in tags and not allow_child:
        return False
    if TAG_ELDERLY in tags and not allow_elderly:
        return False
    if TAG_PRM_ASSIST in tags and not allow_prm_assist:
        return False
    if TAG_BIKE in tags and not allow_bike:
        return False
    return True


def is_sold_out_reason(reason: str) -> bool:
    """Distinguishes true sold-out errors from scrape/network/matching failures."""
    low = (reason or "").lower()
    return any(marker in low for marker in SOLD_OUT_MARKERS)


def parse_grm_wagon_svg_with_meta(
    svg_xml: str,
    ticket_class: int = TARGET_CLASS,
    include_wheelchair: bool = True,
) -> Tuple[List[str], Dict[str, List[str]]]:
    """
    Deterministically parses a GRM wagon SVG XML payload from /grm/wagon/svg/wbnet/...
    Returns (sorted_seats, {seat_no: [tags...]}), extracting attributes from aria-label,
    <eic:special>, and <title>.
    """
    if not svg_xml or not svg_xml.strip():
        return [], {}

    is_class1 = int(ticket_class) == 1
    seats: set[str] = set()
    seat_tags: Dict[str, List[str]] = {}

    def should_skip_class(aria_l: str, d_class_l: str) -> bool:
        if is_class1:
            return (
                "second class" in d_class_l
                or (("klasa 2" in aria_l or "kl. 2" in aria_l) and "klasa 1" not in aria_l and "kl. 1" not in aria_l)
            )
        return (
            "first class" in d_class_l
            or (("klasa 1" in aria_l or "kl. 1" in aria_l) and "klasa 2" not in aria_l and "kl. 2" not in aria_l)
        )

    def add_unique_tag(tag_list: List[str], tag: str) -> None:
        if tag not in tag_list:
            tag_list.append(tag)

    def extract_from_g_attrs(
        aria: str,
        d_class: str,
        text_num: str,
        extra_texts: List[str],
        icon_code: str,
        carriage_code: str,
    ) -> None:
        aria_l = (aria or "").lower()
        d_class_l = (d_class or "").lower()
        if "wolne" not in aria_l:
            return
        if "niedostepne" in aria_l or "niedostępne" in aria_l:
            return
        if should_skip_class(aria_l, d_class_l):
            return

        combined = " | ".join([aria_l] + [t.lower() for t in extra_texts if t])
        is_wheelchair = is_prm_wheelchair_label(combined) or icon_code in ("6", "S6") or carriage_code == "6"
        if is_wheelchair and not include_wheelchair:
            return

        num = (text_num or "").strip()
        if not num or not num.isdigit():
            m = re.search(r"miejsce\s+(\d+)", aria, re.IGNORECASE)
            if m:
                num = m.group(1)
        if num and num.isdigit():
            seats.add(num)
            tags = seat_tags.setdefault(num, [])
            if is_wheelchair:
                add_unique_tag(tags, TAG_WHEELCHAIR)
            elif any(k in combined for k in ("niepełnosprawn", "niepelnosprawn", "ograniczon")):
                add_unique_tag(tags, TAG_PRM_ASSIST)
            if "rower" in combined or icon_code == "S1" or carriage_code == "24":
                add_unique_tag(tags, TAG_BIKE)
            if any(k in combined for k in ("w ciąży", "w ciazy", "ciężarn", "ciezarn")):
                add_unique_tag(tags, TAG_PREGNANT)
            if any(k in combined for k in ("z dzieckiem", "matk", "rodzin")):
                add_unique_tag(tags, TAG_CHILD)
            if any(k in combined for k in ("starsz", "senior")):
                add_unique_tag(tags, TAG_ELDERLY)
            if "strefa ciszy" in combined or "ciszy" in combined:
                add_unique_tag(tags, TAG_QUIET)
            if "okno" in combined:
                add_unique_tag(tags, TAG_WINDOW)
            if "korytarz" in combined:
                add_unique_tag(tags, TAG_AISLE)
            if "stolik" in combined:
                add_unique_tag(tags, TAG_TABLE)

    parsed_via_xml = False
    try:
        cleaned = svg_xml.strip()
        if "<svg" in cleaned and not cleaned.startswith("<svg") and not cleaned.startswith("<?xml"):
            svg_start = cleaned.find("<svg")
            svg_end = cleaned.rfind("</svg>")
            if svg_start != -1 and svg_end != -1:
                cleaned = cleaned[svg_start : svg_end + len("</svg>")]

        root = ET.fromstring(cleaned)
        parsed_via_xml = True
        for elem in root.iter():
            tag = elem.tag.split("}", 1)[-1].lower() if isinstance(elem.tag, str) else ""
            if tag != "g":
                continue
            aria = elem.attrib.get("aria-label", "")
            d_class = elem.attrib.get("data-class", "")
            text_num = ""
            extra_texts: List[str] = []
            icon_code = ""
            carriage_code = ""
            for child in elem.iter():
                c_tag = child.tag.split("}", 1)[-1].lower() if isinstance(child.tag, str) else ""
                if c_tag == "text" and child.text and not text_num:
                    text_num = child.text.strip()
                elif c_tag == "special":
                    for k, v in child.attrib.items():
                        kl = k.lower()
                        if kl in ("pl", "en") and v:
                            extra_texts.append(v.strip())
                        elif kl == "iconcode" and v and not icon_code:
                            icon_code = v.strip().upper()
                        elif kl == "carriagecode" and v and not carriage_code:
                            carriage_code = v.strip()
                    if child.text and child.text.strip():
                        extra_texts.append(child.text.strip())
                elif c_tag == "title" and child.text and child.text.strip():
                    extra_texts.append(child.text.strip())
            extract_from_g_attrs(aria, d_class, text_num, extra_texts, icon_code, carriage_code)
    except ET.ParseError:
        parsed_via_xml = False

    if not parsed_via_xml:
        soup = bs4.BeautifulSoup(svg_xml, "html.parser")
        for g in soup.find_all("g"):
            aria = str(g.get("aria-label") or "")
            d_class = str(g.get("data-class") or "")
            t_el = g.find("text")
            text_num = t_el.get_text(strip=True) if t_el else ""
            extra_texts = []
            icon_code = ""
            carriage_code = ""
            for sp in g.find_all(lambda el: getattr(el, "name", "") and el.name.lower().endswith("special")):
                for attr_name in ("pl", "en"):
                    if sp.get(attr_name):
                        extra_texts.append(str(sp.get(attr_name)).strip())
                if sp.get("iconcode") and not icon_code:
                    icon_code = str(sp.get("iconcode")).strip().upper()
                if sp.get("carriagecode") and not carriage_code:
                    carriage_code = str(sp.get("carriagecode")).strip()
                if sp.get_text(strip=True):
                    extra_texts.append(sp.get_text(strip=True))
            for ttl in g.find_all("title"):
                if ttl.get_text(strip=True):
                    extra_texts.append(ttl.get_text(strip=True))
            extract_from_g_attrs(aria, d_class, text_num, extra_texts, icon_code, carriage_code)

    sorted_seats = sorted(seats, key=lambda x: int(x))
    filtered_tags = {s: seat_tags[s] for s in sorted_seats if seat_tags.get(s)}
    return sorted_seats, filtered_tags


def parse_grm_wagon_svg(svg_xml: str, ticket_class: int = TARGET_CLASS) -> List[str]:
    """
    Deterministically parses a GRM wagon SVG XML payload from /grm/wagon/svg/wbnet/...
    Extracts free seat numbers matching ticket_class (1 or 2) and excludes wheelchair-only PRM seats.
    """
    seats, _ = parse_grm_wagon_svg_with_meta(svg_xml, ticket_class=ticket_class, include_wheelchair=False)
    return seats


def select_grm_target_wagons(
    sklad_data: Dict[str, Any],
    ticket_class: int = TARGET_CLASS,
) -> Tuple[List[Tuple[str, str]], bool]:
    """
    Extracts authoritative available (wagon_no, schema_id) pairs for ticket_class from
    /grm/sklad/wbnet/ JSON, skipping wagonyNiedostepne, wrong class, and WARS/sleepers.
    Returns (targets, had_class_wagons_key).
    """
    class_key = "klasa1" if int(ticket_class) == 1 else "klasa2"
    has_key = class_key in sklad_data
    raw_class_wagons = sklad_data.get(class_key) or []
    unavailable = {str(w).strip() for w in (sklad_data.get("wagonyNiedostepne") or [])}
    raw_schemat = sklad_data.get("wagonySchemat") or {}
    schemat_map = (
        {str(k).strip(): str(v).strip() for k, v in raw_schemat.items()}
        if isinstance(raw_schemat, dict)
        else {}
    )

    # Filter out pure WARS/sleeper wagons if detailed metadata is present in 'wagony'
    service_wagons = set()
    raw_wagony = sklad_data.get("wagony")
    if isinstance(raw_wagony, list):
        for w in raw_wagony:
            if isinstance(w, dict):
                w_no = str(w.get("nr", w.get("number", ""))).strip()
                w_type = str(w.get("typ", w.get("type", ""))).upper()
                if w_no and any(skip in w_type for skip in ["WR", "WARS", "WL", "BC"]):
                    service_wagons.add(w_no)

    targets: List[Tuple[str, str]] = []
    seen = set()
    for w in raw_class_wagons:
        w_no = str(w).strip()
        if not w_no or w_no in seen or w_no in unavailable or w_no in service_wagons:
            continue
        schema_id = schemat_map.get(w_no, "")
        if not schema_id:
            continue
        seen.add(w_no)
        targets.append((w_no, schema_id))

    return targets, has_key


def sanitize_filename(name: str) -> str:
    """Sanitize station/train labels into safe cross-platform filenames."""
    cleaned = re.sub(r"[^a-zA-Z0-9._-]+", "_", name.strip())
    return cleaned.strip("_")[:120] or "snapshot"


def shift_query_time(departure_time: str, offset_minutes: int = -10) -> str:
    """Shifts HH:MM departure time by offset_minutes (default -10 min), clamped at 00:00."""
    clean_time = (departure_time or "").strip() or "12:00"
    m_time = re.match(r"^(\d{1,2}):(\d{2})$", clean_time)
    if not m_time:
        return clean_time
    hh, mm = int(m_time.group(1)), int(m_time.group(2))
    total_min = max(0, min(23 * 60 + 59, hh * 60 + mm + offset_minutes))
    return f"{total_min // 60:02d}:{total_min % 60:02d}"


def build_eic_search_url(
    from_eva: str,
    to_eva: str,
    date_str: str,
    departure_time: str,
    direct_only: bool = True,
) -> str:
    """Builds a pre-filled e-IC 2.0 connection search URL using official EVA station codes."""
    clean_date = (date_str or "").strip()
    if "." in clean_date:
        parts = clean_date.split(".")
        if len(parts) == 3 and len(parts[2]) == 4:
            clean_date = f"{parts[2]}-{parts[1]}-{parts[0]}"

    query_time = shift_query_time(departure_time, offset_minutes=-10)
    polbez = "1" if direct_only else "0"

    return (
        f"https://ebilet.intercity.pl/wyszukiwanie?"
        f"dwyj={clean_date}&swyj={(from_eva or '').strip()}&sprzy={(to_eva or '').strip()}&"
        f"time={query_time}&przy=0&sprzez=&ticket100=1010&ticket50=&polbez={polbez}"
    )


def parse_grm_url_template(sklad_url: str, svg_url: str = "") -> Optional[Dict[str, str]]:
    """
    Extracts host, train category (e.g. IC/EIP/EIC/TLK), and train number from a captured
    /grm/sklad/wbnet/ or /grm/wagon/svg/wbnet/ URL.
    """
    pattern = re.compile(r"^(https?://[^/]+)/grm/(?:sklad|wagon/svg)/wbnet/([^/]+)/([^/]+)/")
    for candidate in (sklad_url or "", svg_url or ""):
        m = pattern.match(candidate.strip())
        if m:
            return {
                "host": m.group(1),
                "category": m.group(2),
                "train_no": m.group(3),
            }
    return None


def format_grm_datetime(date_yyyy_mm_dd: str, hh_mm: str) -> Optional[str]:
    """Formats YYYY-MM-DD (or DD.MM.YYYY) and H:MM/HH:MM into 12-digit YYYYMMDDHHMM for GRM URLs."""
    clean_date = (date_yyyy_mm_dd or "").strip()
    if "." in clean_date:
        parts = clean_date.split(".")
        if len(parts) == 3 and len(parts[2]) == 4:
            clean_date = f"{parts[2]}-{parts[1]}-{parts[0]}"
    m_date = re.match(r"^(\d{4})-(\d{2})-(\d{2})$", clean_date)
    m_time = re.match(r"^(\d{1,2}):(\d{2})$", (hh_mm or "").strip())
    if not m_date or not m_time:
        return None
    hh, mm = int(m_time.group(1)), int(m_time.group(2))
    if not (0 <= hh <= 23 and 0 <= mm <= 59):
        return None
    return f"{m_date.group(1)}{m_date.group(2)}{m_date.group(3)}{hh:02d}{mm:02d}"


def build_grm_sklad_url(
    template: Dict[str, str],
    from_epa: str,
    to_epa: str,
    dep_dt: str,
    arr_dt: str,
) -> str:
    """
    Builds /grm/sklad/wbnet/{category}/{train_no}/{arr_dt}/{from_epa}/{dep_dt}/{to_epa}.
    Note: GRM sklad places arrival datetime before departure station EPA, then dep_dt/to_epa.
    """
    return (
        f"{template['host']}/grm/sklad/wbnet/{template['category']}/{template['train_no']}/"
        f"{arr_dt}/{from_epa}/{dep_dt}/{to_epa}"
    )


def build_grm_svg_url(
    template: Dict[str, str],
    wagon_no: str,
    schema_id: str,
    from_epa: str,
    to_epa: str,
    dep_dt: str,
    arr_dt: str,
) -> str:
    """
    Builds /grm/wagon/svg/wbnet/{category}/{train_no}/{wagon_no}/{schema_id}/{dep_dt}/{arr_dt}/{from_epa}/{to_epa}.
    Note: GRM svg places dep_dt before arr_dt, followed by from_epa/to_epa.
    """
    return (
        f"{template['host']}/grm/wagon/svg/wbnet/{template['category']}/{template['train_no']}/"
        f"{wagon_no}/{schema_id}/{dep_dt}/{arr_dt}/{from_epa}/{to_epa}"
    )
