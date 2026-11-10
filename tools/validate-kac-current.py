#!/usr/bin/env python3
"""Integrity checks for the current Kobolds & Catacombs API catalogue."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STABLE = ("root", "relatedIds", "englishName", "expansion", "setOrder", "formats", "provenance", "mode")

def read(path):
    return json.loads(path.read_text(encoding="utf-8"))

def main():
    catalog = read(ROOT / "cards.json")
    render = read(ROOT / "render.json")
    history = read(ROOT / "history.json")
    assert catalog["schema"] == render["schema"] == history["schema"] == 1
    cards = catalog["cards"]
    rendered = {card["id"]: card for card in render["cards"]}
    assert len(cards) == 204 and len(rendered) == 204
    assert len(list((ROOT / "data").glob("LOOT_*.json"))) == 204
    assert len({card["id"] for card in cards}) == 204
    linebreak_cards = sum("<br></br>" in card.get("text", "") for card in cards)
    assert linebreak_cards == 96, f"Expected 96 existing line-break repairs, got {linebreak_cards}"
    for card in cards:
        cid = card["id"]
        assert rendered[cid]["text"] == card.get("text"), f"{cid}: current render text drift"
        source = read(ROOT / "data" / f"{cid}.json")
        assert source["card"]["id"] == cid
        assert source["card"]["dbfId"] == card["dbfId"]
        for key in STABLE:
            assert key in source, f"{cid}: missing stable metadata {key}"
    # The two explicitly approved current-data corrections.
    illusionist = read(ROOT / "data" / "LOOT_412.json")["card"]
    expected_text = "<b>죽음의 메아리</b>: 내 손에 있는 하수인을 소환합니다.<br></br>그 하수인의 능력치가 1/1이 됩니다."
    expected_raw = "<b>죽음의 메아리</b>: 내 손에 있는 하수인을 소환합니다.\n그 하수인의 능력치가 1/1이 됩니다."
    assert illusionist["text"] == expected_text
    assert illusionist["nativeRawText"] == expected_raw
    assert rendered["LOOT_412"]["text"] == expected_text
    crushing = read(ROOT / "data" / "LOOT_060.json")["card"]
    tag = next(item for item in crushing["nativeTags"] if item["id"] == 215)
    assert tag == {"id": 215, "name": "과부하", "value": 3}
    for location in (cards, render["cards"]):
        card = next(item for item in location if item["id"] == "LOOT_060")
        tag = next(item for item in card["nativeTags"] if item["id"] == 215)
        assert tag == {"id": 215, "name": "과부하", "value": 3}
    print(json.dumps({
        "cards": len(cards),
        "stableMetadataValues": len(cards) * len(STABLE),
        "existingLineBreakCardsPreserved": linebreak_cards,
        "illusionistText": "correct",
        "crushingHandOwnedTag215": 3,
        "historyCards": len(history["cards"]),
    }, ensure_ascii=False))

if __name__ == "__main__":
    main()
