#!/usr/bin/env python3
"""Publish HScard API indexes from canonical per-card JSON.

Source of current card metadata: cards.json (generated current catalogue).
Source of gameplay specs: data/<cardId>.json (release baseline + card.patches).
Legacy reverse-only card.history is accepted temporarily during migration.
Historically introduced races/spell schools are dated in taxonomy-history.json,
materialized as sparse card.history overrides, and published for date lookup.
Never overwrite curated current data with raw client snapshots.
Card release dates are loaded from release-policy.json (Asia/Seoul).


Generated endpoints:
  cards.json   - current cards for WP JSON imports
  render.json  - current cards + sparse history for WebGL and WordPress
  history.json - temporary compatibility endpoint for installed 7.12.10 sites

Do not hand-edit the generated indexes. Do not change cardId/dbfId mappings.
The separate history endpoint is retained only until old WordPress plugins are upgraded.
"""
from __future__ import annotations
import copy
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RENDER_FIELDS = (
    "id", "dbfId", "name", "type", "cardClass", "rarity", "collectible",
    "cost", "text", "attack", "health", "durability", "armor", "art",
    "races", "race", "spellSchool", "set", "nativeTags", "mechanics",
    "watermark", "classes",
)
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
SPEC_FIELDS = (
    "name", "type", "cardClass", "classes", "rarity", "collectible",
    "cost", "attack", "health", "durability", "armor", "text",
    "races", "race", "spellSchool", "mechanics",
)
TAXONOMY_RULE = "taxonomy-introduction"
RACE_KOREAN = {
    "BEAST": "야수", "DEMON": "악마", "DRAGON": "용족", "MURLOC": "멀록",
    "PIRATE": "해적", "TOTEM": "토템", "MECHANICAL": "기계", "MECH": "기계",
    "ELEMENTAL": "정령", "QUILBOAR": "가시멧돼지", "UNDEAD": "언데드",
    "DRAENEI": "드레나이", "NAGA": "나가",
}


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def publish(path: Path, value):
    # Runtime catalogues are compact; the editor-facing catalog stays readable.
    if path.name in ("render.json", "history.json"):
        rendered = json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n"
    else:
        rendered = json.dumps(value, ensure_ascii=False, indent=2) + "\n"
    if path.exists() and path.read_text(encoding="utf-8") == rendered:
        return False
    path.write_text(rendered, encoding="utf-8", newline="\n")
    return True


def read_history(card: dict, name: str):
    versions = card.get("history", [])
    if versions is None:
        return []
    if not isinstance(versions, list):
        raise ValueError(f"{name}: history must be an array")
    for rev in versions:
        if not isinstance(rev, dict) or not isinstance(rev.get("changes"), dict):
            raise ValueError(f"{name}: invalid history entry")
        if not rev["changes"] or not any(x in rev for x in ("from", "before")):
            raise ValueError(f"{name}: history has no changes or date boundary")
        if any(k in rev and (not isinstance(rev[k], str) or not DATE_RE.fullmatch(rev[k])) for k in ("from", "before")):
            raise ValueError(f"{name}: invalid history date")
        if all(k in rev for k in ("from", "before")) and rev["from"] >= rev["before"]:
            raise ValueError(f"{name}: invalid history interval")
    return versions


def read_catalog_source(path: Path, record: dict):
    if not path.is_file():
        return None
    data = load(path)
    card = data.get("card") if isinstance(data, dict) else None
    cid = record["id"]
    if not isinstance(card, dict) or card.get("id") != cid:
        raise ValueError(f"{cid}: source card ID mismatch")
    # A separately archived native token is not an asserted DBF mapping. Keep
    # the approved current catalog, but never attach its archived history to
    # a numeric ID or manufacture a DBF ID in the source document.
    if card.get("dbfId") is None and data.get("apiAvailability", "").startswith("standalone historical source;"):
        return None
    if card.get("dbfId") != record.get("dbfId"):
        raise ValueError(f"{cid}: source card ID mismatch")
    return data



def release_for_card(record: dict, policy: dict):
    """Korean-local first playable date; unknown dates are never invented."""
    cid = record["id"]
    rule = policy.get("default", {"date": None, "status": "pending"})
    for prefix in sorted(policy.get("prefixes", {}), key=len, reverse=True):
        if cid.startswith(prefix):
            rule = policy["prefixes"][prefix]
            break
    rule = policy.get("cards", {}).get(cid, rule)
    if not isinstance(rule, dict):
        raise ValueError(f"Invalid release policy for {cid}")
    day = rule.get("date")
    status = rule.get("status", "pending" if day is None else "verified")
    if day is not None:
        if not isinstance(day, str) or not DATE_RE.fullmatch(day):
            raise ValueError(f"{cid}: invalid Korean release date")
        from datetime import date
        date.fromisoformat(day)
    if status not in ("verified", "estimated", "pending"):
        raise ValueError(f"{cid}: invalid release confidence")
    if status == "verified" and not day:
        raise ValueError(f"{cid}: verified date cannot be null")
    return day, status



def read_taxonomy_policy(path: Path):
    """Load calendar-day feature introduction dates, recorded in Asia/Seoul."""
    if not path.exists():
        return None
    policy = load(path)
    if policy.get("schema") != 1 or policy.get("timezone") != "Asia/Seoul":
        raise ValueError("taxonomy-history.json must use schema 1 and Asia/Seoul")
    from datetime import date
    for key in ("minionTypes", "spellSchools"):
        rules = policy.get(key)
        if not isinstance(rules, dict):
            raise ValueError(f"taxonomy-history.json: {key} must be a map")
        for kind, day in rules.items():
            if not isinstance(kind, str) or not isinstance(day, str) or not DATE_RE.fullmatch(day):
                raise ValueError(f"Invalid {key} introduction: {kind}")
            date.fromisoformat(day)
    overrides = policy.get("cardTypeIntroductions", {})
    if not isinstance(overrides, dict):
        raise ValueError("cardTypeIntroductions must be a map")
    for cid, rules in overrides.items():
        if not isinstance(rules, dict):
            raise ValueError(f"{cid}: cardTypeIntroductions must contain a map")
        for tribe, day in rules.items():
            if not isinstance(day, str) or not DATE_RE.fullmatch(day):
                raise ValueError(f"{cid}: invalid tribe date {tribe}")
            date.fromisoformat(day)
    older = policy.get("priorRaces", {})
    if not isinstance(older, dict) or any(not isinstance(k, str) or not isinstance(v, list)
                                           or any(not isinstance(r, str) for r in v)
                                           for k, v in older.items()):
        raise ValueError("priorRaces must be a map of card IDs to race lists")
    return policy


def historic_race_label(original: str, remaining: list[str]) -> str:
    """Keep the existing original/Korean label when it still identifies a surviving type."""
    if not remaining:
        return ""
    if original in remaining or original in (RACE_KOREAN.get(t) for t in remaining):
        return original
    return RACE_KOREAN.get(remaining[0], remaining[0]) if original in RACE_KOREAN.values() else remaining[0]


def taxonomy_revisions(record: dict, release_day: str | None, policy: dict) -> list[dict]:
    """Sparse past overrides. Latest boundary first so the oldest state wins when overlapping."""
    minion_types = policy["minionTypes"]
    spell_schools = policy["spellSchools"]
    races = record.get("races") if record.get("type") == "MINION" else []
    races = races if isinstance(races, list) else []
    # A card can acquire an existing tribe years after the tribe first debuted.
    per_card = policy.get("cardTypeIntroductions", {}).get(record["id"], {})
    race_dates = {race: per_card.get(race, minion_types.get(race)) for race in races}
    previous_races = policy.get("priorRaces", {}).get(record["id"], [])
    spell = record.get("spellSchool") if record.get("type") == "SPELL" else None
    boundaries = set()

    for race in races:
        day = race_dates.get(race)
        if day and (not release_day or release_day < day):
            boundaries.add(day)
    spell_day = spell_schools.get(spell) if isinstance(spell, str) else None
    if spell_day and (not release_day or release_day < spell_day):
        boundaries.add(spell_day)

    revisions = []
    for before in sorted(boundaries, reverse=True):
        changes = {}
        older_races = [race for race in races
                       if not (race_dates.get(race) and before <= race_dates[race])]
        # Older source records can retain the original Korean tribe label in
        # race while omitting it from the current races list. Preserve it.
        for race in reversed(previous_races):
            if race not in older_races:
                older_races.insert(0, race)
        if older_races != races:
            changes["races"] = older_races
            changes["race"] = historic_race_label(record.get("race") or "", older_races)
        if spell_day and before <= spell_day:
            changes["spellSchool"] = ""
        if changes:
            revisions.append({
                "before": before,
                "changes": changes,
                "generatedBy": TAXONOMY_RULE,
            })
    return revisions



def read_patches(card: dict, name: str) -> list[dict]:
    """Validate that each dated patch only stores changed gameplay fields."""
    patches = card.get("patches")
    if not isinstance(patches, list):
        raise ValueError(f"{name}: patches must be an array")
    prev = card.get("releasedAt")
    for event in patches:
        if not isinstance(event, dict) or set(event) != {"date", "changes"}:
            raise ValueError(f"{name}: invalid forward patch shape")
        day, changes = event["date"], event["changes"]
        if not isinstance(day, str) or not DATE_RE.fullmatch(day):
            raise ValueError(f"{name}: invalid patch date")
        from datetime import date
        date.fromisoformat(day)
        if prev is not None and day <= prev:
            raise ValueError(f"{name}: overlapping/unsorted patch dates {prev}, {day}")
        if not isinstance(changes, dict) or not changes:
            raise ValueError(f"{name}: empty changes on {day}")
        if set(changes) - (set(SPEC_FIELDS) | {"legacyArt"}):
            raise ValueError(f"{name}: unknown gameplay patch field")
        prev = day
    return patches


def publish_forward_card(card: dict, current: dict, name: str):
    """Compute latest cache and a disjoint reverse-override history for old clients."""
    patches = read_patches(card, name)
    base = {field: copy.deepcopy(card[field]) for field in SPEC_FIELDS if field in card}
    if "legacyArt" in card:
        base["legacyArt"] = card["legacyArt"]
    states = [base]
    for event in patches:
        next_state = copy.deepcopy(states[-1])
        for field, value in event["changes"].items():
            if field not in next_state:
                raise ValueError(f"{name}: {field} changed but no release baseline")
            if next_state[field] == value:
                raise ValueError(f"{name}: redundant patch for {field} on {event['date']}")
            next_state[field] = copy.deepcopy(value)
        states.append(next_state)
    latest = states[-1]
    for field in SPEC_FIELDS:
        if field in latest:
            current[field] = copy.deepcopy(latest[field])
        else:
            current.pop(field, None)
    backward = []
    for i, event in enumerate(patches):
        old = states[i]
        differences = {field: copy.deepcopy(value) for field, value in old.items()
                       if value != latest.get(field)}
        if not differences:
            continue
        start = card.get("releasedAt") if i == 0 else patches[i - 1]["date"]
        revision = {"before": event["date"], "changes": differences}
        if start:
            revision["from"] = start
        backward.append(revision)
    return backward


def main():
    source_path = ROOT / "cards.json"
    catalog = load(source_path)
    if catalog.get("schema") != 1 or not isinstance(catalog.get("cards"), list):
        raise ValueError("Unexpected cards.json schema")
    detailed = ROOT / "data"
    release_policy_path = ROOT / "release-policy.json"
    release_policy = load(release_policy_path) if release_policy_path.exists() else None
    taxonomy_policy = read_taxonomy_policy(ROOT / "taxonomy-history.json")
    if release_policy is not None and release_policy.get("timezone") != "Asia/Seoul":
        raise ValueError("Release dates must use Korea (Asia/Seoul)")
    release_counts = {"verified": 0, "estimated": 0, "pending": 0}
    changed_individual = 0
    history = {}
    renderer = []
    seen = set()
    for record in catalog["cards"]:
        if not isinstance(record, dict) or not isinstance(record.get("id"), str):
            raise ValueError("Invalid card in cards.json")
        cid = record["id"]
        if cid in seen:
            raise ValueError(f"Duplicate card ID {cid}")
        seen.add(cid)
        release_date = release_for_card(record, release_policy)[0] if release_policy is not None else record.get("releasedAt")
        path = detailed / (cid + ".json")
        data = read_catalog_source(path, record)
        revisions = []
        if data is None:
            if record.get("history"):
                raise ValueError(f"{cid}: historical data requires {path.name}")
            # Preserve cards that have no detailed source yet.
            record.pop("history", None)
        else:
            card = data["card"]
            # Preserve the approved current card catalogue exactly. Individual
            # JSON owns historical changes only until current data is reconciled.
            if "patches" in card:
                if "history" in card:
                    raise ValueError(f"{cid}: forward patches cannot coexist with legacy reverse history")
                revisions = publish_forward_card(card, record, cid)
            else:
                # Transitional support for un-migrated cards only.
                revisions = read_history(card, cid)
                if taxonomy_policy is not None:
                    curated = [rev for rev in revisions if rev.get("generatedBy") != TAXONOMY_RULE]
                    revisions = curated + taxonomy_revisions(record, release_date, taxonomy_policy)
                    if revisions != card.get("history", []):
                        card["history"] = revisions
                    revisions = read_history(card, cid)
            if revisions:
                history[str(record["dbfId"])] = {"id": cid, "history": revisions}
            elif record.get("history"):
                # Avoid silently discarding previously migrated patches.
                raise ValueError(f"{cid}: history exists in cards.json but not data/{cid}.json")
            record.pop("history", None)
        render_card = {field: record[field] for field in RENDER_FIELDS if field in record}
        # A single runtime endpoint: the current card and its sparse dated
        # differences are delivered together. Absence of history means current.
        if data is not None and revisions:
            render_card["history"] = revisions
        if release_policy is not None:
            release_date, release_status = release_for_card(record, release_policy)
            record["releasedAt"] = release_date
            record["releaseStatus"] = release_status
            render_card["releasedAt"] = release_date
            # Consumers must not hide cards based on an estimated release date.
            render_card["releaseStatus"] = release_status
            release_counts[release_status] += 1
            if data is not None:
                card["releasedAt"] = release_date
                card["releaseStatus"] = release_status
                if publish(path, data):
                    changed_individual += 1
        renderer.append(render_card)

    files = {
        "cards.json": catalog,
        "render.json": {"schema": 1, "set": catalog["set"], "cards": renderer},
        "history.json": {"schema": 1, "set": catalog["set"], "cards": history},
    }
    changed = [name for name, value in files.items() if publish(ROOT / name, value)]
    print(json.dumps({
        "set": catalog["set"], "cards": len(renderer),
        "datedCards": len(history),
        "published": changed, "releaseDates": release_counts,
        "individualCardsUpdated": changed_individual,
    }, ensure_ascii=False))

if __name__ == "__main__":
    main()
