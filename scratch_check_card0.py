import json

with open("scratch_session_cards.json", "r", encoding="utf-8") as f:
    cards = json.load(f)

c0 = cards[0]
for idx, w in enumerate(c0['words']):
    print(f"[{idx}] row_id={w.get('row_id')} all_row_ids={w.get('all_row_ids')} lem={w.get('lemma')} inf={w.get('inflected')[:30]} trans={repr(w.get('translation'))}")
