import base64
import copy
import gzip
import importlib.util
import json
import sys
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location("build_api_v2",ROOT/"tools/build-api-v2.py")
BUILD=importlib.util.module_from_spec(spec)
sys.modules[spec.name]=BUILD
spec.loader.exec_module(BUILD)

def read(path):
    return json.loads(path.read_text(encoding="utf-8"))

def dates(source):
    return sorted(k for k in source if BUILD.DATE_RE.fullmatch(k))

def replay_forward(source):
    keys=dates(source)
    state=copy.deepcopy(source[keys[0]]["state"])
    out=[]
    for i,day in enumerate(keys):
        if i:
            entry=source[day]
            for field,value in entry.get("changes",{}).items():
                state[field]=copy.deepcopy(value)
            for field in entry.get("removedFields",[]):
                state.pop(field,None)
        out.append((day,copy.deepcopy(state)))
    return out

class KoboldsSchemaV2Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog=read(ROOT/"cards.json")
        cls.records={x["id"]:x for x in cls.catalog["cards"]}
        cls.sources={p.stem:read(p) for p in sorted((ROOT/"data").glob("LOOT_*.json"))}
        cls.policy=read(ROOT/"sources/kac-history-v2.json")
        encoded=(ROOT/"sources/kac-tag-evidence-v1.json.gz.b64").read_text(encoding="ascii").strip()
        cls.evidence=json.loads(gzip.decompress(base64.b64decode(encoded)))
        cls.render=read(ROOT/"render.json")
        cls.history=read(ROOT/"history.json")
        cls.legacy=read(ROOT/"legacy.json")

    def test_complete_card_set_and_stable_metadata(self):
        self.assertEqual(len(self.records),204)
        self.assertEqual(len(self.sources),204)
        self.assertEqual(sum(len([k for k in ("root","relatedIds","englishName","expansion","setOrder","formats","provenance","mode") if k in x["_meta"]]) for x in self.sources.values()),1632)
        self.assertEqual(sum(len(g["cards"]) for g in self.evidence["tagEvents"]),89)
        self.assertEqual(len(self.evidence["observations"]),13)
        stable=("root","relatedIds","englishName","expansion","setOrder","formats","provenance","mode")
        for cid,source in self.sources.items():
            self.assertEqual(source["schemaVersion"],2,cid)
            self.assertEqual(source["current"]["dbfId"],self.records[cid]["dbfId"],cid)
            for field,value in source["current"].items():
                if field in self.records[cid]:
                    self.assertEqual(self.records[cid][field],value,f"{cid}:{field}:approved current cache")
            for field in stable:
                self.assertIn(field,source["_meta"],f"{cid}:{field}")
            self.assertEqual(source["nativeTags"],source["current"]["nativeTags"],cid)

    def test_initial_states_are_type_canonical(self):
        for cid,source in self.sources.items():
            first=source[dates(source)[0]]["state"]
            kind=first["type"]
            if kind=="SPELL":
                for field in ("armor","attack","durability","health","race","races"):
                    self.assertNotIn(field,first,f"{cid}:{field}")
            elif kind=="MINION":
                for field in ("armor","durability","spellSchool"):
                    self.assertNotIn(field,first,f"{cid}:{field}")
            elif kind=="WEAPON":
                for field in ("armor","health","race","races","spellSchool"):
                    self.assertNotIn(field,first,f"{cid}:{field}")
                self.assertNotIn("health",first,f"{cid}:weapon health alias")
            elif kind=="HERO":
                self.assertNotIn("health",first,f"{cid}:hero display health")

    def test_forward_and_legacy_interval_replay_agree(self):
        for cid,source in self.sources.items():
            forward=replay_forward(source)
            revisions,details=BUILD.replay_v2(source,self.records[cid],cid)
            for revision in revisions:
                target=dict(next(state for day,state in forward if day==revision["from"]))
                # Restrict this source-state round trip to the documented card
                # state fields; the public-render test separately checks the
                # full current record and its actual date overrides.
                recovered={field:copy.deepcopy(self.records[cid][field])
                           for field in BUILD.STATE_FIELDS if field in self.records[cid]}
                recovered.update({field:copy.deepcopy(value) for field,value in details["currentState"].items()
                                  if field in ("race","races","mechanics")})
                for field in ("nativeRawText","nativeTextBuilder"):
                    if self.sources[cid].get(field) is not None:
                        recovered[field]=copy.deepcopy(self.sources[cid][field])
                recovered.update(copy.deepcopy(revision["changes"]))
                for field in revision.get("removedFields",[]):
                    recovered.pop(field,None)
                for field,value in target.items():
                    if field in details["incomplete"]:
                        continue
                    actual=recovered.get(field,BUILD.MISSING)
                    self.assertEqual(actual,value,f"{cid}:{revision['from']}:{field}")
                for field in BUILD.STATE_FIELDS:
                    if field in recovered and field not in target and field not in details["incomplete"]:
                        self.assertIn(field,revision.get("removedFields",[]),f"{cid}:{revision['from']}:{field}")

    def test_known_release_and_patch_corrections(self):
        for cid in ("LOOT_357","LOOT_357l","LOOT_998h","LOOT_998j","LOOT_998k","LOOT_998l"):
            self.assertEqual(self.records[cid]["releasedAt"],"2017-11-07")
            self.assertEqual(self.sources[cid]["_meta"]["releasedAt"],"2017-11-07")
        for cid in ("LOOT_091t","LOOT_091t1t","LOOT_091t2t","LOOT_209t"):
            self.assertIn("2023-02-15",dates(self.sources[cid]))
            self.assertNotIn("2022-11-30",dates(self.sources[cid]))
        self.assertIn("2022-04-06",dates(self.sources["LOOT_516"]))

    def test_confirmed_new_events_and_separate_wording(self):
        self.assertEqual(len(self.policy["events"]),27)
        self.assertEqual(len(self.policy["dateCorrections"]),5)
        self.assertIn("2022-04-06",dates(self.sources["LOOT_504"]))
        self.assertIn("2022-04-27",dates(self.sources["LOOT_504"]))
        self.assertNotIn("2022-04-06",dates(self.sources["LOOT_504t"]))
        self.assertNotIn("2022-04-27",dates(self.sources["LOOT_504t"]))
        raw_k=self.sources["LOOT_998k"]["2024-10-18"]["changes"]["nativeRawText"]
        raw_l_2024=self.sources["LOOT_998l"]["2024-10-18"]["changes"]["nativeRawText"]
        raw_l_2026=self.sources["LOOT_998l"]["2026-03-11"]["changes"]["nativeRawText"]
        self.assertEqual(raw_k,"[x]<b>도발</b>, <b>전투의 함성:</b> 내 손에 있는\n<b>모든</b> 카드를 <b>전설</b> 하수인으로\n교체합니다. 그 카드들의 비용이\n(1) 감소합니다.")
        self.assertEqual(raw_l_2024,"[x]카드 3장을 뽑습니다.\n그 카드들의 비용을\n(3) 감소시킵니다.")
        self.assertEqual(raw_l_2026,"[x]카드를 3장 뽑습니다.\n그 카드들의 비용을\n1로 만듭니다.")
        self.assertFalse(self.sources["LOOT_998l"]["2024-10-18"]["changes"]["text"].startswith("[x]"))
        self.assertFalse(self.sources["LOOT_998l"]["2026-03-11"]["changes"]["text"].startswith("[x]"))
        self.assertEqual(self.sources["LOOT_998k"]["2024-10-18"]["nativeRawTextChanges"]["after"],raw_k)
        self.assertEqual(self.sources["LOOT_998l"]["2024-10-18"]["nativeRawTextChanges"]["after"],raw_l_2024)
        self.assertEqual(self.sources["LOOT_998l"]["2026-03-11"]["nativeRawTextChanges"]["after"],raw_l_2026)
        rendered={x["id"]:x for x in self.render["cards"]}
        for cid,day,raw in (("LOOT_998k","2024-10-18",raw_k),
                            ("LOOT_998l","2024-10-18",raw_l_2024),
                            ("LOOT_998l","2026-03-11",raw_l_2026)):
            public_revision=next(x for x in rendered[cid]["history"] if x["from"]==day)
            self.assertEqual(public_revision["nativeRawTextChanges"]["after"],raw)
            dbf=str(self.records[cid]["dbfId"])
            legacy_patch=next(x for x in self.legacy["cards"][dbf]["patches"] if x["date"]==day)
            history_patch=next(x for x in self.history["cards"][dbf]["patches"] if x["date"]==day)
            self.assertEqual(legacy_patch["nativeRawTextChanges"]["after"],raw)
            self.assertEqual(history_patch["nativeRawTextChanges"]["after"],raw)
        self.assertEqual(self.sources["LOOT_504"]["_meta"]["functionalEvents"][0]["date"],"2022-04-06")

    def test_undated_loot013_raw_text_observation_is_exact_and_not_dated(self):
        source=self.sources["LOOT_013"]
        launch=source["2017-12-08"]["state"]["nativeRawText"]
        current=source["nativeRawText"]
        self.assertEqual(launch,"<b>도발</b>, <b>전투의 함성:</b>\n내 영웅에게\n피해를 2 줍니다.")
        self.assertEqual(current,"<b>도발</b>, <b>전투의 함성</b>:&\n내 영웅에게\n피해를 2 줍니다.")
        self.assertEqual(source["current"]["text"],"<b>도발</b>, <b>전투의 함성</b>:<br></br>내 영웅에게<br></br>피해를 2 줍니다.")
        observation=next(x for x in source["_meta"]["undatedObservations"] if x["topic"]=="loot013-battlecry-markup")
        self.assertIsNone(observation["date"])
        self.assertEqual(observation["before"],launch)
        self.assertEqual(observation["after"],current)
        revisions,details=BUILD.replay_v2(source,self.records["LOOT_013"],"LOOT_013")
        self.assertIn("nativeRawText",details["incomplete"])
        release_revision=next(x for x in revisions if x["from"]=="2017-12-08")
        self.assertEqual(release_revision["changes"]["nativeRawText"],launch)
        self.assertEqual(release_revision["changes"]["text"],source["2017-12-08"]["state"]["text"])

    def test_card_set_tag_183_is_not_native_text_builder(self):
        source=self.sources["LOOT_060"]
        launch=source["2017-12-08"]
        self.assertNotIn("nativeTextBuilder",launch["state"])
        tag=next(x for x in launch["ownedTags"] if x["id"]==183)
        self.assertEqual(tag["value"],1004)
        self.assertEqual(source["nativeTextBuilder"],0)
        self.assertEqual(self.sources["LOOT_313"]["current"]["race"],"정령, 야수")
        self.assertEqual(self.sources["LOOT_313"]["current"]["races"],["ELEMENTAL","BEAST"])
        for cid,doc in self.sources.items():
            self.assertNotIn("nativeTextBuilder",doc[dates(doc)[0]]["state"],cid)

    def test_undated_raw_text_differences_are_captured_for_all_cards(self):
        source=self.sources["LOOT_014"]
        launch=source["2017-12-08"]["state"]["nativeRawText"]
        current=source["nativeRawText"]
        observation=next(x for x in source["_meta"]["undatedObservations"] if x["topic"]=="native-raw-text-current-vs-dated")
        self.assertEqual(launch,"<b>전투의 함성:</b> 카드를 1장 뽑습니다. 내 영웅에게 피해를 2 줍니다.")
        self.assertEqual(current,"<b>전투의 함성:</b> 카드를 뽑습니다. 내 영웅에게 피해를 2 줍니다.")
        self.assertIsNone(observation["date"])
        self.assertEqual(observation["before"],launch)
        self.assertEqual(observation["after"],current)
        self.assertIn("nativeRawText",source["_meta"]["historyIncompleteFields"])
        text_observation=next(x for x in source["_meta"]["undatedObservations"] if x["topic"]=="display-text-current-vs-dated")
        self.assertIsNone(text_observation["date"])
        self.assertEqual(text_observation["before"],"<b>전투의 함성:</b> 카드를 1장 뽑습니다. 내 영웅에게 피해를 2 줍니다.")
        self.assertEqual(text_observation["after"],"<b>전투의 함성:</b> 카드를 뽑습니다. 내 영웅에게 피해를 2 줍니다.")
        self.assertIn("text",source["_meta"]["historyIncompleteFields"])

    def test_primary_race_enum_and_current_display_are_separate(self):
        self.assertEqual(self.sources["LOOT_516"]["_meta"]["currentPrimaryRace"]["value"],92)
        self.assertEqual(self.sources["LOOT_056"]["2022-11-30"]["changes"]["race"],"야수")
        self.assertEqual(self.sources["LOOT_313"]["2026-02-10"]["changes"]["race"],"야수")
        self.assertEqual(self.sources["LOOT_329"]["2018-08-03"]["changes"]["race"],"")
        self.assertEqual(self.sources["LOOT_209t"]["2023-02-15"]["changes"]["races"],["DRAGON","UNDEAD"])
        self.assertEqual(self.sources["LOOT_375"]["_meta"]["currentPrimaryRace"]["value"],2)
        self.assertEqual(self.records["LOOT_111"]["race"],"기계, 야수")
        self.assertEqual(self.records["LOOT_111"]["races"],["MECHANICAL","BEAST"])
        self.assertEqual(self.sources["LOOT_111"]["_meta"]["currentCanonicalRaces"],["MECHANICAL","BEAST"])
        self.assertEqual(self.records["LOOT_209t"]["race"],"언데드, 용족")
        self.assertEqual(self.records["LOOT_209t"]["races"],["UNDEAD","DRAGON"])
        self.assertEqual(self.sources["LOOT_209t"]["_meta"]["currentCanonicalRaces"],["DRAGON","UNDEAD"])
        self.assertEqual(self.records["LOOT_313"]["race"],"정령, 야수")
        self.assertEqual(self.records["LOOT_313"]["races"],["ELEMENTAL","BEAST"])
        self.assertEqual(self.sources["LOOT_313"]["_meta"]["currentCanonicalRaces"],["BEAST","ELEMENTAL"])

    def test_current_repairs_and_linebreaks_are_preserved(self):
        self.assertEqual(sum("<br></br>" in c.get("text","") for c in self.records.values()),97)
        illusionist=self.records["LOOT_412"]
        self.assertEqual(illusionist["text"],"<b>죽음의 메아리</b>: 내 손에 있는 하수인을 소환합니다.<br></br>그 하수인의 능력치가 1/1이 됩니다.")
        self.assertEqual(self.sources["LOOT_412"]["nativeRawText"],"<b>죽음의 메아리</b>: 내 손에 있는 하수인을 소환합니다.\n그 하수인의 능력치가 1/1이 됩니다.")
        tag=next(x for x in self.records["LOOT_060"]["nativeTags"] if x["id"]==215)
        self.assertEqual(tag,{"id":215,"name":"과부하","value":3})

    def test_raw_tag_deltas_and_thirteen_observation_groups_are_synchronized(self):
        self.assertEqual(self.sources["LOOT_008"]["2021-03-26"]["nativeTagChanges"]["1635"],
                         {"before":{"present":False},"after":{"present":True,"value":6}})
        self.assertEqual(self.sources["LOOT_093"]["2021-03-26"]["nativeTagChanges"]["48"],
                         {"before":{"present":True,"value":5},"after":{"present":True,"value":4}})
        self.assertEqual(self.sources["LOOT_504"]["2022-04-06"]["nativeTagChanges"]["846"],
                         {"before":{"present":False},"after":{"present":True,"value":1}})
        self.assertEqual(self.sources["LOOT_504"]["2022-04-06"]["nativeTagChanges"]["1114"],
                         {"before":{"present":True,"value":1},"after":{"present":False}})
        exact="<b>잔상</b>\n아군 하수인을 비용이 (1) 높은 하수인으로 변신시킵니다."
        self.assertIn("\n",exact)
        self.assertNotIn("\\n",exact)
        self.assertEqual(self.sources["LOOT_504"]["2022-04-27"]["nativeRawTextChanges"]["after"],exact)
        loot504_gap=next(x for x in self.legacy["cards"][str(self.records["LOOT_504"]["dbfId"])]["patches"]
                         if x["date"]=="2022-04-06")["tagHistoryGaps"]["nativeTags"]["1114"]
        self.assertEqual(loot504_gap["lastKnownState"],{"present":False})
        self.assertEqual(loot504_gap["observedBeforeState"],{"present":True,"value":1})
        self.assertIsNone(loot504_gap["transitionDate"])
        self.assertIsNone(loot504_gap["unknownInterval"]["start"])
        loot504_render_revision=next(x for x in {x["id"]:x for x in self.render["cards"]}["LOOT_504"]["history"]
                                      if x["from"]=="2022-04-06")
        loot504_history_revision=next(x for x in self.history["cards"][str(self.records["LOOT_504"]["dbfId"])]["history"]
                                      if x["from"]=="2022-04-06")
        self.assertEqual(loot504_render_revision["tagHistoryGaps"]["nativeTags"]["1114"],loot504_gap)
        self.assertEqual(loot504_history_revision["tagHistoryGaps"]["nativeTags"]["1114"],loot504_gap)
        self.assertEqual(self.sources["LOOT_504"]["2022-04-27"]["changes"]["nativeRawText"],exact)
        self.assertEqual(self.sources["LOOT_504"]["2022-04-27"]["changes"]["text"],
                         "<b>잔상</b><br></br>아군 하수인을 비용이 (1) 높은 하수인으로 변신시킵니다.")
        self.assertNotIn("2022-04-06",dates(self.sources["LOOT_504t"]))
        self.assertNotIn("2022-04-27",dates(self.sources["LOOT_504t"]))
        self.assertTrue(any(x["findingId"]=="KNC-D004" for x in self.sources["LOOT_504t"]["_meta"]["observations"]))
        self.assertEqual(len({x["findingId"] for source in self.sources.values()
                              for x in source["_meta"].get("observations",[])}),13)
        self.assertNotIn("pending", " ".join(x["_meta"]["tagHistoryStatus"] for x in self.sources.values()))
        self.assertEqual(self.sources["LOOT_504"]["_meta"]["tagHistoryStatus"],
                         "confirmed event deltas synchronized; intervening snapshots are not claimed exhaustive")
        for source in self.sources.values():
            for day,entry in source.items():
                if not isinstance(entry,dict) or not re.fullmatch(r"\\d{4}-\\d{2}-\\d{2}",day):
                    continue
                if "tagSnapshotEvidence" in entry:
                    self.assertIsInstance(entry["tagSnapshotEvidence"],list)
        self.assertEqual(self.sources["LOOT_504"]["_meta"]["currentStringEvidence"]["name"],
                         {"present":True,"value":"불안정한 진화"})
        self.assertEqual(self.sources["LOOT_504"]["nativeRawText"],
                         "<b>잔상</b>, 아군 하수인을 비용이 (1) 높은 하수인으로 변신시킵니다.")
        raw_observation=next(x for x in self.sources["LOOT_504"]["_meta"]["undatedObservations"]
                             if x["topic"]=="native-raw-text-current-vs-dated")
        self.assertIsNone(raw_observation["date"])
        self.assertEqual(raw_observation["before"],exact)
        self.assertEqual(raw_observation["after"],self.sources["LOOT_504"]["nativeRawText"])
        revisions,details=BUILD.replay_v2(self.sources["LOOT_504"],self.records["LOOT_504"],"LOOT_504")
        self.assertIn("nativeRawText",details["incomplete"])
        self.assertEqual(next(x for x in revisions if x["from"]=="2022-04-27")["changes"]["nativeRawText"],exact)
        self.assertNotIn("2022-04-06",dates(self.sources["LOOT_504t"]))

    def test_public_render_history_replays_every_card_date_interval(self):
        rendered={x["id"]:x for x in self.render["cards"]}
        self.assertEqual(len(rendered),204)
        self.assertEqual(len(self.legacy["cards"]),204)
        self.assertEqual(len(self.history["cards"]),204)
        for cid,source in self.sources.items():
            card=rendered[cid]
            revisions=card.get("history",[])
            _,details=BUILD.replay_v2(source,self.records[cid],cid)
            for day,state in replay_forward(source):
                applicable=next((x for x in revisions if x["from"]<=day and
                                 (x.get("before") is None or day<x["before"])),None)
                public=copy.deepcopy(card)
                public.pop("history",None)
                if applicable:
                    public.update(copy.deepcopy(applicable["changes"]))
                    for field in applicable.get("removedFields",[]):
                        public.pop(field,None)
                for field in BUILD.STATE_FIELDS:
                    if field in ("nativeRawText","nativeTextBuilder"):
                        continue
                    self.assertEqual(public.get(field,BUILD.MISSING),state.get(field,BUILD.MISSING),
                                     f"{cid}:{day}:{field}")
                expected_tags=details["tagStatesByDay"][day]
                self.assertEqual(public.get("nativeTags",[]),expected_tags["nativeTags"],f"{cid}:{day}:nativeTags")
                self.assertEqual(public.get("referencedTags",[]),expected_tags["referencedTags"],f"{cid}:{day}:referencedTags")
            legacy=self.legacy["cards"][str(self.records[cid]["dbfId"])]
            self.assertEqual(legacy["id"],cid)
            initial=legacy["initialSpec"]
            native={x["id"]:x["value"] for x in initial.get("nativeTags",[])}
            referenced={x["id"]:x["value"] for x in initial.get("referencedTags",[])}
            self.assertTrue(isinstance(initial.get("nativeTags"),list),f"{cid}: nativeTags missing")
            self.assertTrue(isinstance(initial.get("referencedTags"),list),f"{cid}: referencedTags missing")
            self.assertTrue(isinstance(initial.get("ownedTagRecords"),list),f"{cid}: owned tag records missing")
            self.assertTrue(isinstance(initial.get("referencedTagRecords"),list),f"{cid}: referenced tag records missing")
            self.assertTrue(isinstance(initial.get("launchStrings"),dict),f"{cid}: launch string-tag evidence missing")
            self.assertFalse(set(native)&set(referenced),f"{cid}: owned/reference tag identity collision")
            touched_native=set()
            touched_referenced=set()
            for patch in legacy["patches"]:
                for tag_id,delta in patch.get("nativeTagChanges",{}).items():
                    tag_id=int(tag_id); touched_native.add(tag_id)
                    before={"present":tag_id in native}
                    if tag_id in native: before["value"]=native[tag_id]
                    if delta["before"]!=before:
                        gap=patch.get("tagHistoryGaps",{}).get("nativeTags",{}).get(str(tag_id))
                        self.assertIsNotNone(gap,f"{cid}:native-gap:{tag_id}:{patch['date']}")
                        self.assertEqual(gap["lastKnownState"],before)
                        self.assertEqual(gap["observedBeforeState"],delta["before"])
                        self.assertIsNone(gap["transitionDate"])
                    if delta["after"].get("present"):
                        native[tag_id]=delta["after"].get("value")
                    else:
                        native.pop(tag_id,None)
                for tag_id,delta in patch.get("referencedTagChanges",{}).items():
                    tag_id=int(tag_id); touched_referenced.add(tag_id)
                    before={"present":tag_id in referenced}
                    if tag_id in referenced: before["value"]=referenced[tag_id]
                    if delta["before"]!=before:
                        gap=patch.get("tagHistoryGaps",{}).get("referencedTags",{}).get(str(tag_id))
                        self.assertIsNotNone(gap,f"{cid}:referenced-gap:{tag_id}:{patch['date']}")
                        self.assertEqual(gap["lastKnownState"],before)
                        self.assertEqual(gap["observedBeforeState"],delta["before"])
                        self.assertIsNone(gap["transitionDate"])
                    if delta["after"].get("present"):
                        referenced[tag_id]=delta["after"].get("value")
                    else:
                        referenced.pop(tag_id,None)
            current_native={x["id"]:x.get("value") for x in source.get("nativeTags",[])}
            current_referenced={x["id"]:x.get("value") for x in source.get("referencedTags",[])}
            for tag_id in touched_native:
                self.assertEqual(native.get(tag_id,BUILD.MISSING),current_native.get(tag_id,BUILD.MISSING),f"{cid}:native:{tag_id}")
            for tag_id in touched_referenced:
                self.assertEqual(referenced.get(tag_id,BUILD.MISSING),current_referenced.get(tag_id,BUILD.MISSING),f"{cid}:referenced:{tag_id}")
        legacy_by_id={x["id"]:x for x in self.legacy["cards"].values()}
        l008=legacy_by_id["LOOT_008"]
        initial_008={x["id"]:x["value"] for x in l008["initialSpec"]["nativeTags"]}
        self.assertNotIn(1635,initial_008)
        patch_008=next(x for x in l008["patches"] if x["date"]=="2021-03-26")
        self.assertEqual(patch_008["nativeTagChanges"]["1635"],
                         {"before":{"present":False},"after":{"present":True,"value":6}})
        l093=legacy_by_id["LOOT_093"]
        initial_093={x["id"]:x["value"] for x in l093["initialSpec"]["nativeTags"]}
        self.assertEqual(initial_093[48],4)
        patch_093_2018=next(x for x in l093["patches"] if x["date"]=="2018-05-23")
        self.assertEqual(patch_093_2018["nativeTagChanges"]["48"],
                         {"before":{"present":True,"value":4},"after":{"present":True,"value":5}})
        patch_093=next(x for x in l093["patches"] if x["date"]=="2021-03-26")
        self.assertEqual(patch_093["nativeTagChanges"]["48"],
                         {"before":{"present":True,"value":5},"after":{"present":True,"value":4}})
        rendered={x["id"]:x for x in self.render["cards"]}
        def public_at(cid,day):
            card=copy.deepcopy(rendered[cid])
            revisions=card.pop("history",[])
            revision=next(x for x in revisions if x["from"]<=day and
                          (x.get("before") is None or day<x["before"]))
            card.update(copy.deepcopy(revision["changes"]))
            for field in revision.get("removedFields",[]): card.pop(field,None)
            return card
        self.assertNotIn(1635,{x["id"]:x["value"] for x in public_at("LOOT_008","2017-12-08")["nativeTags"]})
        self.assertEqual({x["id"]:x["value"] for x in public_at("LOOT_008","2021-03-26")["nativeTags"]}[1635],6)
        self.assertEqual({x["id"]:x["value"] for x in public_at("LOOT_093","2017-12-08")["nativeTags"]}[48],4)
        self.assertEqual({x["id"]:x["value"] for x in public_at("LOOT_093","2018-05-23")["nativeTags"]}[48],5)
        self.assertEqual({x["id"]:x["value"] for x in public_at("LOOT_093","2021-03-26")["nativeTags"]}[48],4)

    def test_public_race_history_uses_primary_tag_not_multitribe_display(self):
        rendered={x["id"]:x for x in self.render["cards"]}
        expected={
            "LOOT_111":("기계",["MECHANICAL"]),
            "LOOT_209t":("용족",["DRAGON"]),
            "LOOT_313":("",[]),
        }
        for cid,(race,races) in expected.items():
            revisions=rendered[cid]["history"]
            release=next(x for x in revisions if x["from"]=="2017-12-08")
            self.assertEqual(release["changes"].get("race"),race,cid)
            self.assertEqual(release["changes"].get("races",[]),races,cid)
        r209=next(x for x in rendered["LOOT_209t"]["history"] if x["from"]=="2023-02-15")
        self.assertEqual(r209["changes"]["race"],"용족")
        self.assertEqual(r209["changes"]["races"],["DRAGON","UNDEAD"])
        r313=next(x for x in rendered["LOOT_313"]["history"] if x["from"]=="2026-02-10")
        self.assertEqual(r313["changes"]["race"],"야수")
        self.assertEqual(r313["changes"]["races"],["BEAST","ELEMENTAL"])

if __name__=="__main__":
    unittest.main()
