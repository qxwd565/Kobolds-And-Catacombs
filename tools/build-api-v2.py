#!/usr/bin/env python3
"""Build schema-1 compatibility endpoints from date-keyed Kobolds schema v2."""
from __future__ import annotations
import copy
import json
import re
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
DATE_RE=re.compile(r"^\d{4}-\d{2}-\d{2}$")
RENDER_FIELDS=(
 "id","dbfId","name","type","cardClass","rarity","collectible","cost","text",
 "attack","health","durability","armor","art","races","race","spellSchool",
 "set","nativeTags","mechanics","watermark","classes",
)
STATE_FIELDS=(
 "name","type","cardClass","classes","rarity","collectible","cost","attack",
 "health","durability","armor","text","races","race","spellSchool","mechanics",
 "nativeRawText","nativeTextBuilder",
)
MISSING=object()

def read(path):
 return json.loads(path.read_text(encoding="utf-8"))

def publish(path,value):
 compact=path.name in ("render.json","history.json")
 rendered=(json.dumps(value,ensure_ascii=False,separators=(",",":")) if compact else json.dumps(value,ensure_ascii=False,indent=2) )+"\n"
 if not path.exists() or path.read_text(encoding="utf-8")!=rendered:
  path.write_text(rendered,encoding="utf-8",newline="\n")
  return True
 return False

def timeline_dates(source):
 return sorted(k for k in source if DATE_RE.fullmatch(k))

def public_tag_states(source):
 dates=timeline_dates(source)
 first=source[dates[0]]
 owned={int(x["id"]):copy.deepcopy(x) for x in first.get("ownedTags",[])}
 referenced={int(x["id"]):copy.deepcopy(x) for x in first.get("referencedTags",[])}
 current_owned={int(x["id"]):copy.deepcopy(x) for x in source.get("nativeTags",[])}
 current_referenced={int(x["id"]):copy.deepcopy(x) for x in source.get("referencedTags",[])}
 output={}
 def rows(values,current):
  result=[]
  for tag_id,item in sorted(values.items()):
   template=current.get(tag_id,item)
   row={"id":tag_id}
   if template.get("name") is not None: row["name"]=template["name"]
   if "value" in item: row["value"]=copy.deepcopy(item["value"])
   result.append(row)
  return result
 output[dates[0]]={"nativeTags":rows(owned,current_owned),
                   "referencedTags":rows(referenced,current_referenced)}
 for day in dates[1:]:
  entry=source[day]
  for key,values in (("nativeTagChanges",owned),("referencedTagChanges",referenced)):
   for raw_id,delta in entry.get(key,{}).items():
    tag_id=int(raw_id)
    after=delta["after"]
    if after.get("present"):
     template=current_owned.get(tag_id,{}) if key=="nativeTagChanges" else current_referenced.get(tag_id,{})
     item={"id":tag_id}
     if template.get("name") is not None: item["name"]=template["name"]
     if after.get("value") is not None: item["value"]=copy.deepcopy(after["value"])
     values[tag_id]=item
    else:
     values.pop(tag_id,None)
  output[day]={"nativeTags":rows(owned,current_owned),
               "referencedTags":rows(referenced,current_referenced)}
 return output

def tag_history_gaps(source,cid):
 dates=timeline_dates(source)
 first=source[dates[0]]
 states={
  "nativeTags":{int(x["id"]):copy.deepcopy(x.get("value")) for x in first.get("ownedTags",[]) if x.get("value") is not None},
  "referencedTags":{int(x["id"]):copy.deepcopy(x.get("value")) for x in first.get("referencedTags",[]) if x.get("value") is not None},
 }
 result={}
 for day in dates[1:]:
  entry=source[day]
  day_gaps={}
  for change_key,state_key in (("nativeTagChanges","nativeTags"),("referencedTagChanges","referencedTags")):
   for raw_id,delta in entry.get(change_key,{}).items():
    tag_id=int(raw_id)
    known={"present":tag_id in states[state_key]}
    if tag_id in states[state_key]:
     known["value"]=copy.deepcopy(states[state_key][tag_id])
    observed=copy.deepcopy(delta["before"])
    if known!=observed:
     evidence=entry.get("tagSnapshotEvidence",[])
     if isinstance(evidence,dict): evidence=[evidence]
     day_gaps.setdefault(state_key,{})[str(tag_id)]={
      "lastKnownState":known,
      "observedBeforeState":observed,
      "transitionDate":None,
      "unknownInterval":{"start":None,"end":day},
      "beforeSnapshotCommits":[x.get("before",{}).get("commit") for x in evidence if x.get("before",{}).get("commit")],
      "note":"The immutable before-snapshot has this state, but launch plus previously dated deltas do not establish when it entered that state. No intermediate date is inferred."
     }
    after=delta["after"]
    if after.get("present"):
     states[state_key][tag_id]=copy.deepcopy(after.get("value"))
    else:
     states[state_key].pop(tag_id,None)
  if day_gaps: result[day]=day_gaps
 return result

def replay_v2(source,record,cid):
 dates=timeline_dates(source)
 if not dates:
  raise ValueError(f"{cid}: schema v2 has no date-keyed initial state")
 first=source[dates[0]]
 if not isinstance(first,dict) or not isinstance(first.get("state"),dict):
  raise ValueError(f"{cid}: {dates[0]} must contain a full state")
 state=copy.deepcopy(first["state"])
 states=[(dates[0],copy.deepcopy(state),first)]
 for day in dates[1:]:
  entry=source[day]
  if not isinstance(entry,dict) or not isinstance(entry.get("changes",{}),dict):
   raise ValueError(f"{cid}: invalid sparse event on {day}")
  for field,value in entry.get("changes",{}).items():
   state[field]=copy.deepcopy(value)
  removed=entry.get("removedFields",[])
  if not isinstance(removed,list) or any(not isinstance(x,str) for x in removed):
   raise ValueError(f"{cid}: invalid removedFields on {day}")
  for field in removed:
   state.pop(field,None)
  states.append((day,copy.deepcopy(state),entry))
 meta=source.get("_meta",{})
 incomplete=set(meta.get("historyIncompleteFields",[]))
 current=source.get("current",{})
 latest=states[-1][1]
 current_state={}
 for field in latest:
  if field=="nativeRawText":
   if source.get("nativeRawText") is not None: current_state[field]=copy.deepcopy(source["nativeRawText"])
   else: incomplete.add(field)
  elif field=="nativeTextBuilder":
   if source.get("nativeTextBuilder") is not None: current_state[field]=copy.deepcopy(source["nativeTextBuilder"])
   else: incomplete.add(field)
  elif field=="mechanics" and field not in current and "currentMechanics" in meta:
   current_state[field]=copy.deepcopy(meta["currentMechanics"])
  elif field=="race" and "currentPrimaryRace" in meta:
   current_state[field]=copy.deepcopy(meta["currentPrimaryRace"].get("race") or "")
  elif field=="races" and "currentCanonicalRaces" in meta:
   current_state[field]=copy.deepcopy(meta["currentCanonicalRaces"])
  elif field in current:
   current_state[field]=copy.deepcopy(current[field])
  else:
   incomplete.add(field)
 undated_by_field={}
 for observation in meta.get("undatedObservations",[]):
  if observation.get("date") is None and isinstance(observation.get("fields"),list):
   for field in observation["fields"]:
    undated_by_field[field]=observation
 for field,value in latest.items():
  if field not in current_state:
   if field in incomplete:
    continue
   raise ValueError(f"{cid}: current cache is missing dated field {field}")
  current_value=current_state[field]
  if current_value==value:
   continue
  observation=undated_by_field.get(field)
  if observation is not None and observation.get("before")==value and observation.get("after")==current_value:
   continue
  raise ValueError(f"{cid}: current cache {field}={current_value!r} differs from dated replay {value!r} without an exact undated observation")
 # A current value may have appeared after an absent launch field. Preserve the
 # absence and mark it explicitly rather than inventing a launch default.
 for field in STATE_FIELDS:
  if field in current and field not in latest:
   incomplete.add(field)
 public_baseline={}
 for field in STATE_FIELDS:
  if field in record:
   public_baseline[field]=copy.deepcopy(record[field])
  elif field=="nativeRawText" and source.get("nativeRawText") is not None:
   public_baseline[field]=copy.deepcopy(source["nativeRawText"])
  elif field=="nativeTextBuilder" and source.get("nativeTextBuilder") is not None:
   public_baseline[field]=copy.deepcopy(source["nativeTextBuilder"])
  elif field=="mechanics" and "currentMechanics" in meta:
   public_baseline[field]=copy.deepcopy(meta["currentMechanics"])
 gaps_by_day=tag_history_gaps(source,cid)
 tag_states_by_day=public_tag_states(source)
 revisions=[]
 for index,(day,state,entry) in enumerate(states):
  # Every public interval is a self-contained override of the current card.
  # Do not omit a re-addition merely because its value equals today's cache:
  # an earlier interval may explicitly remove that field.
  changes={field:copy.deepcopy(value) for field,value in state.items() if field in STATE_FIELDS}
  removed=[field for field in public_baseline if field not in state]
  native_changes=entry.get("nativeTagChanges")
  referenced_changes=entry.get("referencedTagChanges")
  tag_evidence=entry.get("tagSnapshotEvidence")
  changes["nativeTags"]=copy.deepcopy(tag_states_by_day[day]["nativeTags"])
  changes["referencedTags"]=copy.deepcopy(tag_states_by_day[day]["referencedTags"])
  revision={"from":day,"changes":changes}
  if index+1<len(states): revision["before"]=states[index+1][0]
  if removed: revision["removedFields"]=sorted(removed)
  if native_changes: revision["nativeTagChanges"]=copy.deepcopy(native_changes)
  if referenced_changes: revision["referencedTagChanges"]=copy.deepcopy(referenced_changes)
  if tag_evidence: revision["tagSnapshotEvidence"]=copy.deepcopy(tag_evidence)
  if day in gaps_by_day: revision["tagHistoryGaps"]=copy.deepcopy(gaps_by_day[day])
  if entry.get("nativeRawTextChanges"): revision["nativeRawTextChanges"]=copy.deepcopy(entry["nativeRawTextChanges"])
  if entry.get("enTextChanges"): revision["enTextChanges"]=copy.deepcopy(entry["enTextChanges"])
  if entry.get("sources"): revision["sources"]=copy.deepcopy(entry["sources"])
  if entry.get("eventTypes"): revision["eventTypes"]=copy.deepcopy(entry["eventTypes"])
  if incomplete: revision["historyIncompleteFields"]=sorted(incomplete)
  revisions.append(revision)
 return revisions,{"currentState":current_state,"incomplete":sorted(incomplete),"datedStates":len(states),"tagHistoryGaps":gaps_by_day,"tagStatesByDay":tag_states_by_day}

def main():
 catalog=read(ROOT/"cards.json")
 if catalog.get("schema")!=1 or not isinstance(catalog.get("cards"),list):
  raise ValueError("cards.json must remain schema 1 for compatibility")
 policy=read(ROOT/"release-policy.json")
 if policy.get("timezone")!="Asia/Seoul":
  raise ValueError("Release policy must use Asia/Seoul")
 renderer=[]
 history={}
 legacy={}
 seen=set()
 release_counts={"verified":0,"estimated":0,"pending":0}
 for record in catalog["cards"]:
  cid=record["id"]
  if cid in seen: raise ValueError(f"duplicate card ID {cid}")
  seen.add(cid)
  path=ROOT/"data"/(cid+".json")
  source=read(path)
  if source.get("schemaVersion")!=2:
   raise ValueError(f"{cid}: expected schemaVersion 2")
  if source.get("current",{}).get("id")!=cid or source["current"].get("dbfId")!=record.get("dbfId"):
   raise ValueError(f"{cid}: current cache identity mismatch")
  revisions,details=replay_v2(source,record,cid)
  timeline=timeline_dates(source)
  first=source[timeline[0]]
  initial_spec=copy.deepcopy(first["state"])
  initial_spec["nativeTags"]=[copy.deepcopy(x) for x in first.get("ownedTags",[]) if x.get("value") is not None]
  initial_spec["referencedTags"]=[copy.deepcopy(x) for x in first.get("referencedTags",[]) if x.get("value") is not None]
  initial_spec["ownedTagRecords"]=copy.deepcopy(first.get("ownedTags",[]))
  initial_spec["referencedTagRecords"]=copy.deepcopy(first.get("referencedTags",[]))
  initial_spec["launchStrings"]=copy.deepcopy(source.get("_meta",{}).get("launchStringEvidence",{}))
  patches=[]
  for patch_day in timeline[1:]:
   event=source[patch_day]
   patch={"date":patch_day,"changes":copy.deepcopy(event.get("changes",{}))}
   if event.get("removedFields"): patch["removedFields"]=copy.deepcopy(event["removedFields"])
   for key in ("nativeTagChanges","referencedTagChanges","tagSnapshotEvidence","nativeRawTextChanges","enTextChanges","sources","eventTypes"):
    if event.get(key): patch[key]=copy.deepcopy(event[key])
   if patch_day in details["tagHistoryGaps"]: patch["tagHistoryGaps"]=copy.deepcopy(details["tagHistoryGaps"][patch_day])
   if patch["changes"] or any(key in patch for key in ("removedFields","nativeTagChanges","referencedTagChanges","tagSnapshotEvidence","nativeRawTextChanges","enTextChanges")):
    patches.append(patch)
  legacy[str(record["dbfId"])]={"id":cid,"initialSpec":initial_spec,"patches":patches}
  history[str(record["dbfId"])]={"id":cid,"history":revisions,"historyIncompleteFields":details["incomplete"],
                                 "initialSpec":initial_spec,"patches":patches}
  render_card={field:copy.deepcopy(record[field]) for field in RENDER_FIELDS if field in record}
  if "referencedTags" not in render_card:
   render_card["referencedTags"]=copy.deepcopy(source.get("referencedTags",[]))
  if "mechanics" not in render_card and "currentMechanics" in source.get("_meta",{}):
   render_card["mechanics"]=copy.deepcopy(source["_meta"]["currentMechanics"])
  if revisions: render_card["history"]=revisions
  # Preserve the exact approved current card cache; only release metadata is
  # updated from the explicit Korea-local policy.
  day=source.get("_meta",{}).get("releasedAt",record.get("releasedAt"))
  status=source.get("_meta",{}).get("releaseStatus",record.get("releaseStatus","verified"))
  if day is not None and (not isinstance(day,str) or not DATE_RE.fullmatch(day)):
   raise ValueError(f"{cid}: invalid release date {day!r}")
  if status not in release_counts: raise ValueError(f"{cid}: invalid release status {status!r}")
  record["releasedAt"]=day
  record["releaseStatus"]=status
  render_card["releasedAt"]=day
  render_card["releaseStatus"]=status
  release_counts[status]+=1
  renderer.append(render_card)
 files={
  "cards.json":catalog,
  "render.json":{"schema":1,"set":catalog["set"],"cards":renderer},
  "history.json":{"schema":1,"set":catalog["set"],"cards":history},
  "legacy.json":{"schema":1,"set":catalog["set"],"cards":legacy},
 }
 changed=[name for name,value in files.items() if publish(ROOT/name,value)]
 print(json.dumps({"cards":len(renderer),"datedCards":len(history),"historyIntervals":sum(len(x["history"]) for x in history.values()),"legacyCards":len(legacy),"legacyPatches":sum(len(x["patches"]) for x in legacy.values()),"published":changed,"releaseDates":release_counts},ensure_ascii=False))

if __name__=="__main__":
 main()
