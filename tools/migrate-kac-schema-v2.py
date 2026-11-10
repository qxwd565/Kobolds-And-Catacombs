#!/usr/bin/env python3
"""Migrate Kobolds & Catacombs card sources to date-keyed schema v2.

The launch state is parsed from immutable HearthSim CardDefs snapshots.
Current caches and stable metadata are copied, never reconstructed from launch data.
"""
from __future__ import annotations
import argparse
import copy
import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
STABLE = ("root", "relatedIds", "englishName", "expansion", "setOrder", "formats", "provenance", "mode")
CLASS = {2:"DRUID",3:"HUNTER",4:"MAGE",5:"PALADIN",6:"PRIEST",7:"ROGUE",8:"SHAMAN",9:"WARLOCK",10:"WARRIOR",12:"NEUTRAL",14:"DEMONHUNTER",16:"DEATHKNIGHT"}
TYPE = {3:"HERO",4:"MINION",5:"SPELL",7:"WEAPON",10:"HERO_POWER",39:"LOCATION"}
RARITY = {0:"INVALID",1:"COMMON",2:"FREE",3:"RARE",4:"EPIC",5:"LEGENDARY"}
RACE = {2:"DRAENEI",11:"UNDEAD",14:"MURLOC",15:"DEMON",17:"MECHANICAL",18:"ELEMENTAL",20:"BEAST",21:"TOTEM",22:"QUILBOAR",23:"PIRATE",24:"DRAGON",25:"ALL",92:"NAGA"}
RACE_KO = {"MURLOC":"멀록","DEMON":"악마","MECHANICAL":"기계","ELEMENTAL":"정령","BEAST":"야수","TOTEM":"토템","QUILBOAR":"가시멧돼지","PIRATE":"해적","DRAGON":"용족","NAGA":"나가","UNDEAD":"언데드","DRAENEI":"드레나이"}
SCHOOL = {1:"ARCANE",2:"FIRE",3:"FROST",4:"NATURE",5:"HOLY",6:"SHADOW",7:"FEL"}
TAG_NAMES = {45:"생명력",47:"공격력",48:"마나 비용",187:"내구도",200:"종족",215:"과부하",1635:"주문 속성",2540:"보조 종족 정령",2542:"보조 종족 야수",2534:"보조 종족 언데드"}

def read(path):
    return json.loads(path.read_text(encoding="utf-8"))

def write(path, value):
    rendered = json.dumps(value, ensure_ascii=False, indent=2) + "\n"
    if not path.exists() or path.read_text(encoding="utf-8") != rendered:
        path.write_text(rendered, encoding="utf-8", newline="\n")
        return True
    return False

def local(tag):
    return tag.rsplit("}", 1)[-1]

def scalar(value):
    if value is None:
        return None
    try:
        if re.fullmatch(r"-?\d+", value):
            return int(value)
        if re.fullmatch(r"-?\d+\.\d+", value):
            return float(value)
    except TypeError:
        pass
    return value

def parse_entity(elem):
    owned, referenced, strings = [], [], {}
    for node in list(elem):
        kind = local(node.tag)
        if kind not in ("Tag", "ReferencedTag"):
            continue
        raw_id = node.attrib.get("enumID")
        if raw_id is None:
            continue
        tag_id = int(raw_id)
        value = scalar(node.attrib.get("value"))
        locales = {}
        for child in list(node):
            locale = local(child.tag)
            if locale in ("koKR", "enUS"):
                locales[locale] = child.text or ""
        record = {"id":tag_id, "value":value}
        if kind == "Tag":
            owned.append(record)
        else:
            referenced.append(record)
        if tag_id in (184,185):
            strings[str(tag_id)] = {
                "tagPresent":True,
                "locales":{locale:{"present":locale in locales,"xmlText":locales.get(locale)} for locale in ("koKR","enUS")}
            }
            if tag_id in (184,185):
                for locale in ("koKR","enUS"):
                    if locale in locales:
                        strings[str(tag_id)]["locales"][locale]["nativeRawText"] = locales[locale].replace("\\n","\n")
    return {"owned":owned,"referenced":referenced,"strings":strings}

def read_xml(path, wanted):
    found = {}
    for _, elem in ET.iterparse(path, events=("end",)):
        if local(elem.tag) == "Entity":
            cid = elem.attrib.get("CardID")
            if cid in wanted:
                found[cid] = parse_entity(elem)
            elem.clear()
    missing = sorted(wanted - set(found))
    if missing:
        raise ValueError(f"{path}: missing {len(missing)} required card IDs: {missing[:12]}")
    return found

def tags_by_id(entity):
    return {tag["id"]:tag.get("value") for tag in entity["owned"]}

def race_list(entity):
    tags = tags_by_id(entity)
    primary = tags.get(200)
    result = []
    if primary is not None and primary in RACE:
        result.append(RACE[primary])
    secondary = ((2540,"ELEMENTAL"),(2542,"BEAST"),(2534,"UNDEAD"))
    for tag_id, tribe in secondary:
        value = tags.get(tag_id)
        if value not in (None,0,False) and tribe not in result:
            result.append(tribe)
    return result

def primary_race_label(entity):
    primary = tags_by_id(entity).get(200)
    race = RACE.get(primary) if primary is not None else None
    return RACE_KO.get(race, race) if race else ""

def launch_state(entity, cid):
    tags = tags_by_id(entity)
    kind = TYPE.get(tags.get(202))
    if kind is None:
        raise ValueError(f"{cid}: unsupported or absent launch card type {tags.get(202)!r}")
    state = {"type":kind}
    # CARDNAME and CARDTEXT_INHAND are localized string Tags, not numeric Tag values.
    # Their presence and exact locale values are retained separately in _meta.launchStrings.
    if 199 in tags and tags[199] in CLASS:
        state["cardClass"] = CLASS[tags[199]]
    if 203 in tags and tags[203] in RARITY:
        state["rarity"] = RARITY[tags[203]]
    if 321 in tags:
        state["collectible"] = bool(tags[321])
    if 48 in tags: state["cost"] = tags[48]
    if kind in ("MINION","HERO","HERO_POWER","LOCATION") and 47 in tags: state["attack"] = tags[47]
    if kind in ("MINION","LOCATION") and 45 in tags: state["health"] = tags[45]
    if kind == "WEAPON":
        if 187 in tags: state["durability"] = tags[187]
        elif 45 in tags: state["durability"] = tags[45]
        if 47 in tags: state["attack"] = tags[47]
    if kind in ("HERO","HERO_POWER") and 292 in tags: state["armor"] = tags[292]
    if kind == "MINION":
        races = race_list(entity)
        state["races"] = races
        state["race"] = primary_race_label(entity)
    if kind == "SPELL" and 1635 in tags:
        state["spellSchool"] = SCHOOL.get(tags[1635], "")
    strings = entity["strings"]
    name = strings.get("185",{}).get("locales",{}).get("koKR",{})
    if name.get("present"):
        state["name"] = name.get("nativeRawText", name.get("xmlText"))
    text = strings.get("184",{}).get("locales",{}).get("koKR",{})
    if text.get("present"):
        raw = text.get("nativeRawText", text.get("xmlText"))
        state["nativeRawText"] = raw
        state["text"] = raw.replace("[x]","").replace("\\n","<br></br>").replace("\n","<br></br>") if raw is not None else ""
    # Canonical type rules: no zero placeholders for fields that do not apply.
    if kind == "SPELL":
        for field in ("armor","attack","durability","health","race","races"):
            state.pop(field,None)
    elif kind == "MINION":
        for field in ("armor","durability","spellSchool"):
            state.pop(field,None)
    elif kind == "WEAPON":
        for field in ("armor","health","race","races","spellSchool"):
            state.pop(field,None)
    elif kind == "HERO":
        # Hero health is a native source tag, not a canonical displayed stat.
        state.pop("health",None)
    return state

def merge_event(events, date, changes, source=None, event_type=None):
    target = next((e for e in events if e["date"] == date), None)
    if target is None:
        target = {"date":date,"changes":{}}
        events.append(target)
    for field, value in changes.items():
        if field in target["changes"] and target["changes"][field] != value:
            raise ValueError(f"Conflicting {field} values on {date}: {target['changes'][field]!r} vs {value!r}")
        target["changes"][field] = copy.deepcopy(value)
    if source:
        target.setdefault("sources",[])
        if source not in target["sources"]:
            target["sources"].append(source)
    if event_type:
        target.setdefault("eventTypes",[])
        if event_type not in target["eventTypes"]:
            target["eventTypes"].append(event_type)

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--marine-launch",required=True)
    ap.add_argument("--main-launch",required=True)
    ap.add_argument("--current",required=True)
    args=ap.parse_args()
    policy=read(ROOT/"sources/kac-history-v2.json")
    catalog=read(ROOT/"cards.json")
    records={c["id"]:c for c in catalog["cards"]}
    wanted=set(records)
    marine_ids=set(policy["releaseOverrides"])
    launch_marine=read_xml(Path(args.marine_launch),marine_ids)
    launch_main=read_xml(Path(args.main_launch),wanted-marine_ids)
    current_xml=read_xml(Path(args.current),wanted)
    changed=0
    corrections_applied=set()
    for cid,record in records.items():
        path=ROOT/"data"/(cid+".json")
        source=read(path)
        if source.get("schemaVersion")==2:
            if source.get("current",{}).get("id")!=cid or source.get("_meta",{}).get("schemaVersion")!=2:
                raise ValueError(f"{cid}: invalid existing schema v2 source")
            continue
        old=source.get("card",{})
        if source.get("schemaVersion")!=1:
            raise ValueError(f"{cid}: unsupported schemaVersion {source.get('schemaVersion')}")
        launch=(launch_marine if cid in marine_ids else launch_main)[cid]
        state=launch_state(launch,cid)
        # Validate the supplied research's release-state assertions; never replace
        # a source observation with a guessed value.
        for field,expected in policy.get("initialAssertions",{}).get(cid,{}).items():
            actual=state.get(field,object())
            if actual != expected:
                raise ValueError(f"{cid}: launch XML {field}={actual!r}, expected {expected!r}")
        release=policy.get("releaseOverrides",{}).get(cid,old.get("releasedAt","2017-12-08"))
        events=[]
        for event in old.get("patches",[]):
            day=event["date"]
            for correction in policy.get("dateCorrections",[]):
                if correction["cardId"]==cid and day==correction["from"]:
                    day=correction["to"]
                    corrections_applied.add(correction["cardId"])
            merge_event(events,day,event["changes"],source=old.get("patchSources",{}).get(event["date"]))
        for event in policy.get("events",[])+[x for x in policy.get("wordingObservations",[]) if x.get("date") and x.get("changes")]:
            if event["cardId"]==cid:
                changes=copy.deepcopy(event["changes"])
                if "text" in changes:
                    raw_text=changes.get("nativeRawText",changes["text"].replace("<br></br>","\n"))
                    changes["nativeRawText"]=raw_text
                    changes["text"]=changes["text"].replace("\n","<br></br>")
                merge_event(events,event["date"],changes,event.get("source"),event.get("eventType","gameplay-change"))
        events.sort(key=lambda x:x["date"])
        # A dated race-array event also records the primary Tag 200 label when
        # that primary tribe first appears. Secondary-only additions must not
        # rewrite the primary label.
        current_primary_enum=tags_by_id(current_xml[cid]).get(200)
        current_primary=RACE.get(current_primary_enum)
        current_primary_label=RACE_KO.get(current_primary,current_primary or "") if current_primary else ""
        primary_at_date=state.get("race","")
        for event in events:
            changes=event["changes"]
            if "races" in changes and current_primary in changes["races"]:
                changes["races"]=[current_primary]+[race for race in changes["races"] if race!=current_primary]
            if "race" in changes:
                primary_at_date=changes["race"]
            elif "races" in changes and current_primary in changes["races"] and primary_at_date!=current_primary_label:
                changes["race"]=current_primary_label
                primary_at_date=current_primary_label
        dates={}
        dates[release]={"state":state,"ownedTags":launch["owned"],"referencedTags":launch["referenced"]}
        for event in events:
            if event["date"] <= release:
                raise ValueError(f"{cid}: event {event['date']} is not after release {release}")
            dates[event["date"]]={"changes":event["changes"],"sources":event.get("sources",[]),"eventTypes":event.get("eventTypes",[])}
        # Preserve the curated current display fields verbatim, including race label
        # and races array order. Canonical Tag 200 is stored separately in _meta.
        current_card=copy.deepcopy(record)
        # Compare latest dated display/raw strings with approved current values
        # for all 204 cards in this one migration pass. Unknown transitions stay
        # explicitly undated; neither side is copied over the other.
        latest_raw=state.get("nativeRawText")
        latest_text=state.get("text")
        latest_name=state.get("name")
        for event in events:
            changes=event["changes"]
            if "nativeRawText" in changes:
                latest_raw=changes["nativeRawText"]
            if "text" in changes:
                latest_text=changes["text"]
        current_raw=old.get("nativeRawText")
        undated_text_observations=[]
        if latest_raw != current_raw:
            undated_text_observations.append({
                "topic":"native-raw-text-current-vs-dated",
                "fields":["nativeRawText"],
                "before":latest_raw,
                "after":current_raw,
                "date":None,
                "sourceCommits":[policy["snapshotSources"]["launchMarine" if cid in marine_ids else "launchMain"]["commit"],policy["snapshotSources"]["current"]["commit"]],
                "note":"Immutable launch plus dated events compared with the approved current raw string; transition date is not established."
            })
        if latest_text != record.get("text"):
            undated_text_observations.append({
                "topic":"display-text-current-vs-dated",
                "fields":["text"],
                "before":latest_text,
                "after":record.get("text"),
                "date":None,
                "sourceCommits":[policy["snapshotSources"]["launchMarine" if cid in marine_ids else "launchMain"]["commit"],policy["snapshotSources"]["current"]["commit"]],
                "note":"Immutable launch plus dated events compared with the approved current display text; transition date is not established."
            })
        if latest_name != record.get("name"):
            undated_text_observations.append({
                "topic":"card-name-current-vs-dated",
                "fields":["name"],
                "before":latest_name,
                "after":record.get("name"),
                "date":None,
                "sourceCommits":[policy["snapshotSources"]["launchMarine" if cid in marine_ids else "launchMain"]["commit"],policy["snapshotSources"]["current"]["commit"]],
                "note":"Immutable CARDNAME launch value compared with the approved current name; transition date is not established."
            })
        stable={key:copy.deepcopy(source[key]) for key in STABLE}
        owner_ids={tag["id"] for tag in current_xml[cid]["owned"]}
        names={tag["id"]:tag.get("name",TAG_NAMES.get(tag["id"],f"Tag {tag['id']}")) for tag in old.get("nativeTags",[])}
        reference_tags=[{"id":tag["id"],"name":names.get(tag["id"],TAG_NAMES.get(tag["id"],f"Tag {tag['id']}")),"value":tag.get("value")} for tag in current_xml[cid]["referenced"]]
        meta={
            **stable,
            "schemaVersion":2,
            "releaseStatus":old.get("releaseStatus","verified"),
            "releasedAt":release,
            "historyIncompleteFields":sorted(
                set(source.get("historyIncompleteFields",[]))|{"nativeTags","referencedTags"}|
                ({"nativeRawText"} if any("text" in event.get("changes",{}) and "nativeRawText" not in event.get("changes",{}) for event in old.get("patches",[])) else set())|
                ({"name"} if "name" in state and record.get("name")!=state["name"] else set())|
                ({field for observation in undated_text_observations for field in observation["fields"]})|
                {field for observation in policy.get("undatedObservations",[]) if cid in observation.get("cardIds",[]) for field in observation.get("fields",[])}
            ),
            "launchStringEvidence":launch["strings"],
            "currentStringEvidence":{"name":{"present":"name" in old.get("card",{}),"value":old.get("card",{}).get("name")},"nativeRawText":{"present":"nativeRawText" in old,"value":current_raw}},
            "launchPrimaryRace":{"tagId":200,"value":tags_by_id(launch).get(200),"race":primary_race_label(launch)},
            "currentPrimaryRace":{"tagId":200,"value":tags_by_id(current_xml[cid]).get(200),"race":primary_race_label(current_xml[cid])},
            "currentCanonicalRaces":race_list(current_xml[cid]) if current_card.get("type")=="MINION" else [],
            "currentTagOrigins":[{"id":t["id"],"source":"owned" if t["id"] in owner_ids else "referenced"} for t in old.get("nativeTags",[])],
            "tagHistoryStatus":"snapshot synchronization pending; see tag-observation source manifest",
            "wordingObservations":[x for x in policy.get("wordingObservations",[]) if x["cardId"]==cid],
            "undatedObservations":[x for x in policy.get("undatedObservations",[]) if cid in x.get("cardIds",[])] + undated_text_observations,
            "editorialSuggestions":[x for x in policy.get("editorialSuggestions",[]) if x["cardId"]==cid],
            "functionalEvents":[x for x in policy.get("events",[]) if x["cardId"]==cid and x.get("eventType")=="functional-rule-change"],
        }
        if cid=="LOOT_504":
            meta["currentMechanics"]=["ECHO"]
        # Preserve the approved current raw text, builder, tag-array order, names,
        # values, and all non-target current fields exactly as they are.
        doc={
            "schemaVersion":2,
            "_meta":meta,
            "current":current_card,
            "nativeTags":copy.deepcopy(old.get("nativeTags",[])),
            "referencedTags":reference_tags,
            "nativeRawText":old.get("nativeRawText"),
            "nativeTextBuilder":old.get("nativeTextBuilder"),
        }
        doc.update(dates)
        if write(path,doc):
            changed+=1
    missing_corrections=[c["cardId"] for c in policy.get("dateCorrections",[]) if c["cardId"] not in corrections_applied]
    if missing_corrections:
        raise ValueError(f"Expected existing patch date corrections were not found: {missing_corrections}")
    print(json.dumps({"cards":len(records),"schemaVersion":2,"changedFiles":changed,"marineLaunch":len(marine_ids),"mainLaunch":len(wanted-marine_ids),"eventsAddedOrMerged":len(policy["events"]),"dateCorrections":len(policy["dateCorrections"]),"wordingObservations":len(policy.get("wordingObservations",[]))},ensure_ascii=False))

if __name__=="__main__":
    main()
