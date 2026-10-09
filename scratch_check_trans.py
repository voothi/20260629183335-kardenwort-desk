import json

with open("scratch_session_cards.json", "r", encoding="utf-8") as f:
    cards = json.load(f)

print(f"Total cards: {len(cards)}")
for c in cards:
    print(f"\n=== Card idx={c.get('index')} seq={c.get('seq_num')} label={c.get('label')} ===")
    for w in c.get('words', []):
        if w.get('lemma') == 'task' or 'task' in w.get('inflected', ''):
            print(f"  row_id={w.get('row_id')} all_ids={w.get('all_row_ids')} inf={w.get('inflected')} lem={w.get('lemma')} trans={repr(w.get('translation'))} prov={repr(w.get('provenance'))}")
            if w.get('row_html'):
                print(f"    row_html trans snippet: {[part for part in w.get('row_html').split('</td>') if 'col-translation' in part]}")
