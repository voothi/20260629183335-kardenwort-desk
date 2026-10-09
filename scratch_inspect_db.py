import sqlite3

db_path = r"U:\voothi\20260629183335-kardenwort-desk\data\kardenwort.db"
conn = sqlite3.connect(db_path)
cur = conn.cursor()

zid = "20261009193715"

print("--- Sessions ---")
cur.execute("SELECT zid, slug, source_language, source_raw_text FROM sessions WHERE zid = ?", (zid,))
row = cur.fetchone()
if row:
    print("Found session:", row[0], row[1], row[2])
    print("Raw text:", row[3])
else:
    print("Session not found by exact ZID. Latest sessions:")
    cur.execute("SELECT zid, slug FROM sessions ORDER BY created_at DESC LIMIT 5")
    for r in cur.fetchall():
        print(" ", r)

print("\n--- Sentences ---")
cur.execute("SELECT sentence_index, sentence_source, sentence_destination, text_provenance FROM sentences WHERE session_zid = ? ORDER BY sentence_index", (zid,))
sents = cur.fetchall()
for s in sents:
    print(f"[{s[0]}] {s[1]}")
    print(f"     -> {s[2]} (prov={s[3]})")

print("\n--- Words for session ---")
cur.execute("""
    SELECT id, sentence_index, token_order, lemma, inflected_form, quotation, word_destination, word_provenance, pos 
    FROM words 
    WHERE session_zid = ? 
    ORDER BY sentence_index, token_order
""", (zid,))
words = cur.fetchall()
for w in words:
    print(f"id={w[0]} sent_idx={w[1]} t_ord={w[2]} lemma={w[3]} inf={w[4]} quot={w[5]} trans={repr(w[6])} prov={w[7]} pos={w[8]}")

conn.close()
