"""Failing-first tests for extract_json robustness (reduce-parse bug).

Each case mirrors a real or plausible LLM output shape. Against the OLD
implementation, cases A/B/D raise JSONDecodeError or return the wrong object;
the fix must make all of them return the real spec."""
from yt_trade_distill.distill import extract_json

REAL = ('{"philosophy_summary":"trend trader","markets":["US equities"],'
        '"entry_rules":[{"id":"x","side":"long"}],"exit_rules":{},"filters":[]}')

cases = {
    # A. leading small object then the real spec — the EXACT traceback signature
    #    (a complete object ends early -> old code throws "Extra data").
    "A_leading_object": '{"note":"here is the merged spec"}\n' + REAL,
    # B. real spec then trailing prose/commentary after the closing brace.
    "B_trailing_prose": REAL + '\n\nLet me know if you want adjustments!',
    # C. clean single fenced object (the common, already-working case).
    "C_fenced_clean": "```json\n" + REAL + "\n```",
    # D. preamble fenced block THEN the real fenced spec (old code grabs the first).
    "D_two_fences": ('Here is a quick summary:\n```json\n{"videos":6}\n```\n\n'
                     'And the full spec:\n```json\n' + REAL + '\n```'),
    # E. bare clean object (no fence, no decoration).
    "E_bare": REAL,
}

failures = 0
for name, text in cases.items():
    try:
        obj = extract_json(text)
        ok = isinstance(obj, dict) and "philosophy_summary" in obj and obj.get("entry_rules")
        print(f"  {name:20} -> {'PASS' if ok else 'WRONG OBJECT: ' + str(list(obj.keys()))}")
        if not ok:
            failures += 1
    except Exception as e:
        print(f"  {name:20} -> RAISED {type(e).__name__}: {e}")
        failures += 1

print(f"\n{failures} failing case(s)")
raise SystemExit(1 if failures else 0)
