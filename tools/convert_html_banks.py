"""Turn two MIT-licensed HTML practice sets into JSON that tools/import_banks.py can read (run once per download).
Usage: python tools/convert_html_banks.py   (reads temp/sources/new/<repo>/, writes temp/sources/<exam>/<name>.json)
  tony-dunlop/claude-certification-practice-tests: <section class="q" data-a="C"> blocks; the scenario card that leads each group is
      put in front of every question that follows it, so no question loses its context.
  thisisrohitsoni/cca-f-mock-exam: the page's own script holds BANK/SCN/DOMAINS; node evaluates the data part (everything before the UI code).
Then add rows to temp/sources/MANIFEST.md and run: python tools/import_banks.py --only=<name> <exam>"""
import glob, html, json, os, re, subprocess, sys, tempfile
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "temp" / "sources"
strip = lambda s: html.unescape(re.sub(r"<[^>]+>", "", s)).strip()


def tony() -> None:
    for f in sorted(glob.glob(str(SRC / "new" / "tony-dunlop_claude-certification-practice-tests" / "*practice*.html"))):
        t = open(f, encoding="utf-8", errors="replace").read()
        out, scen = [], ""
        for m in re.finditer(r'<section class="q"(?: style="[^"]*")?(?: data-a="([A-Z,]*)")?>(.*?)</section>', t, re.S):
            body = m.group(2)
            if not m.group(1):                      # a scenario card: its text leads the questions that follow
                scen = " ".join(strip(p) for p in re.findall(r"<p[^>]*>(.*?)</p>", body, re.S))
                continue
            dom = re.sub(r"^Q\d+\s*", "", strip(re.search(r'<div class="meta">(.*?)</div>', body, re.S).group(1)))
            stem = strip(re.search(r"</div>\s*<p>(.*?)</p>", body, re.S).group(1))
            ex = re.search(r'<div class="ans">(.*?)</div>', body, re.S)
            out.append({"question": (f"{scen} " if scen else "") + stem, "options": [strip(x) for x in re.findall(r"<li[^>]*>(.*?)</li>", body, re.S)],
                        "correct": m.group(1).split(","), "explanation": strip(ex.group(1)) if ex else "", "domain": dom})
        exam = "ccdvf" if "ccdvf" in os.path.basename(f) else "ccarf"
        dst = SRC / exam / f"tonydunlop_{Path(f).stem}.json"
        dst.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
        print(dst.name, len(out))


def rohitsoni() -> None:
    t = open(SRC / "new" / "thisisrohitsoni_cca-f-mock-exam" / "index.html", encoding="utf-8", errors="replace").read()
    js = "\n".join(re.findall(r"<script>(.*?)</script>", t, re.S))
    js = js[:js.index("const TOTAL_SECONDS")]      # the data comes first, the UI code (which needs a DOM) after it
    js += '''
const out=[]; for (const [d,arr] of Object.entries(BANK)) for (const q of arr) out.push({question:(q.s&&SCN[q.s]?SCN[q.s].title+". "+SCN[q.s].body+" ":"")+q.q, options:q.o, correct:q.c, explanation:q.e||"", domain:(DOMAINS[d]||{}).name||d});
console.log(JSON.stringify(out));'''
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as f:
        f.write(js)
    raw = json.loads(subprocess.run(["node", f.name], capture_output=True, text=True, encoding="utf-8", check=True).stdout)
    tag = lambda s: re.sub(r"</?code>", "", s)
    for x in raw:
        x["question"], x["explanation"], x["options"] = tag(x["question"]), tag(x["explanation"]), [tag(o) for o in x["options"]]
        x["correct"] = ["ABCDEF"[i] for i in x["correct"]]
    (SRC / "ccarf" / "rohitsoni_mock.json").write_text(json.dumps(raw, ensure_ascii=False, indent=1), encoding="utf-8")
    print("rohitsoni_mock.json", len(raw))


if __name__ == "__main__":
    tony(); rohitsoni()
