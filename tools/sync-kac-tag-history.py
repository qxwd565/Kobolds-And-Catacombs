#!/usr/bin/env python3
"""Synchronize confirmed raw Tag and representation observations from immutable CardDefs snapshots."""
from __future__ import annotations
import base64, copy, gzip, hashlib, importlib.util, json, re
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location("kac_migrate",ROOT/"tools/migrate-kac-schema-v2.py")
M=importlib.util.module_from_spec(spec)
spec.loader.exec_module(M)

def read(path):
    return json.loads(path.read_text(encoding="utf-8"))

def write(path,value):
    rendered=json.dumps(value,ensure_ascii=False,indent=2)+"\n"
    if not path.exists() or path.read_text(encoding="utf-8")!=rendered:
        path.write_text(rendered,encoding="utf-8",newline="\n")
        return True
    return False

def load_evidence():
    encoded=(ROOT/"sources/kac-tag-evidence-v1.json.gz.b64").read_text(encoding="ascii").strip()
    return json.loads(gzip.decompress(base64.b64decode(encoded)))

def expected_present(obj):
    return bool(obj.get("present",False))

def verify_tag(entity,tag_id,expected,kind="nativeTags",context=""):
    rows=entity["owned" if kind=="nativeTags" else "referenced"]
    actual={int(x["id"]):x.get("value") for x in rows}
    tag_id=int(tag_id)
    if not expected_present(expected):
        if tag_id in actual:
            raise ValueError(f"{context}: tag {tag_id} expected absent, got {actual[tag_id]!r}")
    else:
        if tag_id not in actual:
            raise ValueError(f"{context}: tag {tag_id} expected present")
        value=M.scalar(str(expected.get("value"))) if expected.get("value") is not None else None
        if actual[tag_id]!=value:
            raise ValueError(f"{context}: tag {tag_id} expected {value!r}, got {actual[tag_id]!r}")

def verify_text(entity,locale,expected,context):
    item=entity.get("strings",{}).get("184",{}).get("locales",{}).get(locale,{})
    if not item.get("present",False):
        raise ValueError(f"{context}: CARDTEXT_INHAND {locale} expected present")
    actual=item.get("nativeRawText",item.get("xmlText"))
    if actual!=expected:
        raise ValueError(f"{context}: CARDTEXT_INHAND {locale} mismatch: {actual!r} != {expected!r}")

def snapshot_entities(evidence):
    groups=evidence["tagEvents"]+evidence["observations"]
    commit_ids={}
    commit_hashes={}
    for group in groups:
        for side in ("before","after"):
            commit=group[side]["commit"]
            commit_ids.setdefault(commit,set()).update(
                c.get("id") or c.get("originalCardId") for c in group.get("cards",[])
            )
            expected=group[side]["xmlSha256"]
            if commit in commit_hashes and commit_hashes[commit]!=expected:
                raise ValueError(f"{commit}: conflicting XML SHA-256 evidence")
            commit_hashes[commit]=expected
    parsed={}
    for commit,ids in commit_ids.items():
        path=Path(f"/tmp/CardDefs-{commit}.xml")
        if not path.is_file():
            raise FileNotFoundError(f"Missing immutable snapshot {path}")
        actual=hashlib.sha256(path.read_bytes()).hexdigest()
        if actual!=commit_hashes[commit]:
            raise ValueError(f"{commit}: XML SHA-256 {actual} != evidence {commit_hashes[commit]}")
        parsed[commit]=M.read_xml(path,ids)
    return parsed

def verify_tag_groups(evidence,parsed):
    for group in evidence["tagEvents"]:
        for side in ("before","after"):
            snap=parsed[group[side]["commit"]]
            for card in group["cards"]:
                cid=card["id"]
                entity=snap[cid]
                for key,kind in (("nativeTags","nativeTags"),("referencedTags","referencedTags")):
                    for tag_id,delta in card.get(key,{}).items():
                        verify_tag(entity,tag_id,delta[side],kind,f"{group['day']} {cid} {side}")
    for group in evidence["observations"]:
        for side in ("before","after"):
            snap=parsed[group[side]["commit"]]
            for card in group["cards"]:
                cid=card["originalCardId"]
                entity=snap[cid]
                for field,delta in card.get("fields",{}).items():
                    if field.isdigit():
                        verify_tag(entity,int(field),delta[side],"nativeTags",f"{group['findingId']} {cid} {side}")
                    elif field=="rawText":
                        verify_text(entity,"koKR",delta[side],f"{group['findingId']} {cid} {side}")
                    elif field=="enText":
                        verify_text(entity,"enUS",delta[side],f"{group['findingId']} {cid} {side}")

def merge_delta(target,key,delta,context):
    existing=target.setdefault(key,{})
    for tag_id,value in delta.items():
        if tag_id in existing and existing[tag_id]!=value:
            raise ValueError(f"{context}: conflicting {key} for tag {tag_id}")
        existing[tag_id]=copy.deepcopy(value)

def display_text(raw):
    return raw.replace("[x]","").replace("\\n","<br></br>").replace("\n","<br></br>")

def add_source(target,url):
    if url and url not in target.setdefault("sources",[]):
        target["sources"].append(url)

def add_snapshot_evidence(entry,group,finding_id=None):
    item={"before":copy.deepcopy(group["before"]),"after":copy.deepcopy(group["after"])}
    if finding_id: item["findingId"]=finding_id
    existing=entry.get("tagSnapshotEvidence")
    if existing is None:
        entry["tagSnapshotEvidence"]=item
    else:
        evidence=existing if isinstance(existing,list) else [existing]
        if item not in evidence: evidence.append(item)
        entry["tagSnapshotEvidence"]=evidence
    for side in ("before","after"):
        add_source(entry, f"https://github.com/HearthSim/hsdata/commit/{group[side]['commit']}")

def attach_observation(source,cid,group,card):
    meta=source.setdefault("_meta",{})
    item={"findingId":group["findingId"],"date":group.get("day"),
          "before":copy.deepcopy(group["before"]),"after":copy.deepcopy(group["after"]),
          "cardEvidence":copy.deepcopy(card)}
    items=meta.setdefault("observations",[])
    items[:]=[x for x in items if not (x.get("findingId")==group["findingId"] and x.get("cardEvidence",{}).get("originalCardId")==cid)]
    items.append(item)

def sync_event(source,cid,day,group,card,finding_id=None,event_type=None):
    if day not in source:
        release=source.get("_meta",{}).get("releasedAt")
        if release and day<=release:
            raise ValueError(f"{cid}: observation date {day} is not after release {release}")
        source[day]={"changes":{},"sources":[],"eventTypes":[]}
    entry=source[day]
    for key in ("nativeTags","referencedTags"):
        if card.get(key):
            api_key="nativeTagChanges" if key=="nativeTags" else "referencedTagChanges"
            merge_delta(entry,api_key,card[key],f"{cid} {day}")
    if event_type:
        types=entry.setdefault("eventTypes",[])
        if event_type not in types: types.append(event_type)
    add_snapshot_evidence(entry,group,finding_id)

def main():
    evidence=load_evidence()
    parsed=snapshot_entities(evidence)
    verify_tag_groups(evidence,parsed)
    catalog=read(ROOT/"cards.json")
    records={x["id"]:x for x in catalog["cards"]}
    corrections={(x["cardId"],x["from"]):x["to"] for x in read(ROOT/"sources/kac-history-v2.json").get("dateCorrections",[])}
    changed=0
    synced_events=0
    observations=0
    # The 62 existing and 27 newly researched event groups carry only confirmed
    # owned/referenced tag deltas. No unrelated full-snapshot diff is promoted.
    for group in evidence["tagEvents"]:
        day=group["day"]
        for card in group["cards"]:
            cid=card["id"]
            if cid not in records:
                raise ValueError(f"{cid}: evidence card is not in the 204-card set")
            event_day=corrections.get((cid,day),day)
            path=ROOT/"data"/(cid+".json")
            source=read(path)
            if source.get("schemaVersion")!=2:
                raise ValueError(f"{cid}: expected schema v2 before tag synchronization")
            sync_event(source,cid,event_day,group,card)
            if write(path,source): changed+=1
            synced_events+=1
    # The 13 observation groups are retained individually. Only dated wording
    # observations and the explicitly dated engine metadata group enter timeline.
    dated_types={
        "KNC-D002":"wording","KNC-D003":"wording","KNC-D005":"wording",
        "KNC-D006":"wording","KNC-D008":"observed-engine-metadata","KNC-D012":"wording"
    }
    for group in evidence["observations"]:
        fid=group["findingId"]
        day=group.get("day")
        for card in group["cards"]:
            cid=card["originalCardId"]
            if cid not in records:
                raise ValueError(f"{cid}: observation card is not in the 204-card set")
            path=ROOT/"data"/(cid+".json")
            source=read(path)
            if source.get("schemaVersion")!=2:
                raise ValueError(f"{cid}: expected schema v2 before observation synchronization")
            attach_observation(source,cid,group,card)
            if day and fid in dated_types:
                fields=card.get("fields",{})
                # Create/merge the confirmed observation boundary first. A
                # wording-only date need not already exist in the gameplay timeline.
                sync_event(source,cid,day,group,{"nativeTags":{str(k):v for k,v in fields.items() if k.isdigit()}},fid,dated_types[fid])
                entry=source[day]
                raw=fields.get("rawText",{}).get("after")
                if raw is not None:
                    entry.setdefault("changes",{})["nativeRawText"]=raw
                    entry["changes"]["text"]=display_text(raw)
                # sync_event has already merged tag changes for D008; preserve exact
                # raw/enUS strings and before/after evidence in _meta.observations.
            if fid=="KNC-D004" and cid=="LOOT_504":
                fields=card.get("fields",{})
                delta={str(k):v for k,v in fields.items() if k.isdigit()}
                sync_event(source,cid,"2022-04-06",group,{"nativeTags":delta},fid,"functional-rule-change")
            observations+=1
            if write(path,source): changed+=1
    # Fix the confirmed 2022-04-27 raw string: it is the actual snapshot text,
    # not the later comma-form current string. The current cache is untouched.
    policy_path=ROOT/"sources/kac-history-v2.json"
    policy=read(policy_path)
    for item in policy.get("wordingObservations",[]):
        if item.get("cardId")=="LOOT_504" and item.get("date")=="2022-04-27":
            raw="<b>잔상</b>\n아군 하수인을 비용이 (1) 높은 하수인으로 변신시킵니다."
            item["changes"]={"nativeRawText":raw,"text":display_text(raw)}
    if write(policy_path,policy): changed+=1
    # Every current card name is present in the current cache. Do not confuse it
    # with the sparse launch baseline's CARDNAME evidence.
    for cid in records:
        path=ROOT/"data"/(cid+".json")
        source=read(path)
        meta=source["_meta"]
        current=source["current"]
        name_present="name" in current
        meta["currentStringEvidence"]["name"]={"present":name_present,"value":current.get("name")}
        meta["tagHistoryStatus"]="confirmed event deltas synchronized; intervening snapshots are not claimed exhaustive"
        if write(path,source): changed+=1
    print(json.dumps({"cards":len(records),"confirmedTagEventCardRecords":synced_events,
                      "observationCardRecords":observations,"changedFiles":changed,
                      "verifiedSnapshots":len(parsed),"verifiedSha256":len(parsed)},ensure_ascii=False))

if __name__=="__main__":
    main()
