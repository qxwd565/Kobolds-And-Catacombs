import copy
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

    def test_complete_card_set_and_stable_metadata(self):
        self.assertEqual(len(self.records),204)
        self.assertEqual(len(self.sources),204)
        stable=("root","relatedIds","englishName","expansion","setOrder","formats","provenance","mode")
        for cid,source in self.sources.items():
            self.assertEqual(source["schemaVersion"],2,cid)
            self.assertEqual(source["current"]["dbfId"],self.records[cid]["dbfId"],cid)
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
                recovered=copy.deepcopy(details["currentState"])
                recovered.update(copy.deepcopy(revision["changes"]))
                for field in revision.get("removedFields",[]):
                    recovered.pop(field,None)
                for field,value in target.items():
                    if field in details["incomplete"]:
                        continue
                    actual=recovered.get(field,BUILD.MISSING)
                    self.assertEqual(actual,value,f"{cid}:{revision['from']}:{field}")
                for field in recovered:
                    if field not in target and field not in details["incomplete"]:
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
        self.assertFalse(any("nativeRawText" in revision["changes"] for revision in revisions))
        self.assertFalse(any("text" in revision["changes"] for revision in revisions))

    def test_card_set_tag_183_is_not_native_text_builder(self):
        source=self.sources["LOOT_060"]
        launch=source["2017-12-08"]
        self.assertNotIn("nativeTextBuilder",launch["state"])
        tag=next(x for x in launch["ownedTags"] if x["id"]==183)
        self.assertEqual(tag["value"],1004)
        self.assertEqual(source["nativeTextBuilder"],0)
        self.assertEqual(self.sources["LOOT_313"]["current"]["race"],"정령, 야수")
        self.assertEqual(self.sources["LOOT_313"]["current"]["races"],["BEAST","ELEMENTAL"])
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

if __name__=="__main__":
    unittest.main()
