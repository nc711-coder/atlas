"""Step 4c: a small labelling page in your browser for the 300 tiles from step 4b.

Run from the project folder:  python pipeline/step4c_label_server.py              (the 300 test/held-out tiles)
                              python pipeline/step4c_label_server.py --set train  (round 2 training tiles)
It opens http://localhost:8765 . Press 1-5 to label, Backspace to go back. Every key press is saved
immediately to <DATA>/step4/labels.csv, so you can stop (Ctrl+C in PowerShell) and resume any time.
"""
import argparse
import csv
import json
import threading
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from config import DATA

STEP4 = DATA / "step4"
PORT = 8765
# eval = the 300 test/held-out tiles (5 classes, as originally labelled)
# train = round 2 training tiles (4 classes: bare ground/construction is merged into "other")
SETS = {
    "eval": {"items": "to_label.csv", "labels": "labels.csv",
             "classes": ["built", "parking", "green", "bare_or_construction", "other"]},
    "train": {"items": "to_label_train.csv", "labels": "labels_train.csv",
              "classes": ["built", "parking", "green", "other"]},
}
GUIDE = {
    "built": "any building roof (office, shop, home, warehouse, parking garage). A tall building's visible side wall counts as building.",
    "parking": "surface parking lots, including the driving aisles inside them",
    "green": "trees, grass, planted areas",
    "bare_or_construction": "bare soil, gravel, foundations, active building sites",
    "other": "roads, highways, ramps, sidewalks, water, or no clear majority",
}
ITEMS = STEP4 / SETS["eval"]["items"]
LABELS = STEP4 / SETS["eval"]["labels"]
CLASSES = SETS["eval"]["classes"]
lock = threading.Lock()


def load_items():
    with open(ITEMS, newline="") as f:
        return list(csv.DictReader(f))


def load_labels():
    if not LABELS.exists():
        return {}
    with open(LABELS, newline="") as f:
        return {r["tile_id"]: r for r in csv.DictReader(f)}


def save_label(tile_id, label):
    with lock:
        labels = load_labels()
        labels[tile_id] = {"tile_id": tile_id, "label": label, "labelled_at": datetime.now().isoformat(timespec="seconds")}
        tmp = LABELS.with_suffix(".tmp")
        with open(tmp, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=["tile_id", "label", "labelled_at"])
            w.writeheader()
            w.writerows(labels.values())
        tmp.replace(LABELS)      # write-then-rename, so a crash never leaves a half-written file


PAGE = """<!doctype html><html><head><meta charset="utf-8"><title>Atlas tile labelling</title>
<style>
 body{font-family:system-ui,sans-serif;margin:24px;display:flex;gap:32px;background:#fafafa;color:#222}
 img{width:480px;height:480px;image-rendering:auto;border:1px solid #999;background:#ddd}
 .side{max-width:460px} .btn{display:block;width:100%;text-align:left;margin:6px 0;padding:10px 12px;
 font-size:16px;border:1px solid #bbb;border-radius:6px;background:#fff;cursor:pointer}
 .btn.chosen{background:#ffe8cc;border-color:#d97706} kbd{background:#eee;border:1px solid #bbb;border-radius:4px;padding:1px 6px}
 .muted{color:#666;font-size:14px} .bar{height:8px;background:#e5e5e5;border-radius:4px;margin:8px 0}
 .bar>div{height:8px;background:#2563eb;border-radius:4px}
</style></head><body>
<div><img id="img"><div class="muted" id="tid"></div></div>
<div class="side">
 <h2 style="margin-top:0">What covers most of this tile?</h2>
 <div id="progress"></div><div class="bar"><div id="fill" style="width:0"></div></div>
 <div id="buttons"></div>
 <p class="muted"><kbd>1</kbd>-<kbd>5</kbd> label &middot; <kbd>Backspace</kbd> previous &middot; <kbd>&rarr;</kbd> skip for now<br>
 Each tile is 60 m x 60 m from the 2025 aerial photo. Pick what covers the <b>largest share</b> of it.</p>
 <ul class="muted">%GUIDE%</ul>
 <p class="muted">Shadows: label what is underneath if you can tell; otherwise decide from the rest of the tile.</p>
 <p class="muted"><a id="maps" target="_blank">Open this spot in Google Maps</a> for context (its photo may be from a different year: label what <i>this</i> image shows).</p>
</div>
<script>
const CLASSES = %CLASSES%;
let items = [], labels = {}, i = 0;
async function init(){
  const r = await (await fetch('/api/state')).json();
  items = r.items; labels = r.labels;
  i = items.findIndex(t => !labels[t.tile_id]); if (i < 0) i = items.length - 1;
  document.getElementById('buttons').innerHTML = CLASSES.map((c,k) =>
    `<button class="btn" id="b${k}" onclick="choose(${k})"><kbd>${k+1}</kbd> ${c}</button>`).join('');
  show();
}
function show(){
  const t = items[i], done = Object.keys(labels).length;
  document.getElementById('img').src = '/img/' + t.tile_id + '.png';
  document.getElementById('tid').textContent = `tile ${i+1} of ${items.length}: ${t.tile_id}`;
  document.getElementById('progress').textContent = `${done} of ${items.length} labelled`;
  document.getElementById('fill').style.width = (100*done/items.length) + '%';
  document.getElementById('maps').href = `https://www.google.com/maps/@${t.lat},${t.lng},90m/data=!3m1!1e3`;
  CLASSES.forEach((c,k) => document.getElementById('b'+k).classList.toggle('chosen', labels[t.tile_id] === c));
}
async function choose(k){
  const t = items[i];
  await fetch('/api/label', {method:'POST', body: JSON.stringify({tile_id: t.tile_id, label: CLASSES[k]})});
  labels[t.tile_id] = CLASSES[k];
  const next = items.findIndex((x, j) => j > i && !labels[x.tile_id]);
  i = next >= 0 ? next : Math.min(i + 1, items.length - 1);
  if (Object.keys(labels).length === items.length) alert('All tiles labelled - thank you!');
  show();
}
document.addEventListener('keydown', e => {
  if (e.key >= '1' && e.key <= String(CLASSES.length)) choose(Number(e.key) - 1);
  else if (e.key === 'Backspace') { e.preventDefault(); i = Math.max(0, i - 1); show(); }
  else if (e.key === 'ArrowRight') { i = Math.min(items.length - 1, i + 1); show(); }
});
init();
</script></body></html>"""


def build_page():
    guide = dict(GUIDE)
    if "bare_or_construction" not in CLASSES:
        guide["other"] = "roads, highways, ramps, sidewalks, water, bare ground or construction, or no clear majority"
    items = "".join(f"<li><b>{c}</b>: {guide[c]}</li>" for c in CLASSES)
    return PAGE.replace("%CLASSES%", json.dumps(CLASSES)).replace("%GUIDE%", items)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):        # keep PowerShell quiet
        pass

    def send(self, code, body, ctype):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/":
            self.send(200, build_page().encode(), "text/html; charset=utf-8")
        elif self.path == "/api/state":
            state = {"items": load_items(), "labels": {k: v["label"] for k, v in load_labels().items()}}
            self.send(200, json.dumps(state).encode(), "application/json")
        elif self.path.startswith("/img/"):
            name = self.path[len("/img/"):]
            path = STEP4 / "previews" / name
            if "/" in name or "\\" in name or ".." in name or not path.exists():
                self.send(404, b"not found", "text/plain")
            else:
                self.send(200, path.read_bytes(), "image/png")
        else:
            self.send(404, b"not found", "text/plain")

    def do_POST(self):
        if self.path != "/api/label":
            return self.send(404, b"not found", "text/plain")
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        valid_ids = {r["tile_id"] for r in load_items()}
        if body.get("label") not in CLASSES or body.get("tile_id") not in valid_ids:
            return self.send(400, b"bad label", "text/plain")
        save_label(body["tile_id"], body["label"])
        self.send(200, b"ok", "text/plain")


def main():
    global ITEMS, LABELS, CLASSES
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", choices=SETS, default="eval")
    chosen = SETS[ap.parse_args().set]
    ITEMS, LABELS, CLASSES = STEP4 / chosen["items"], STEP4 / chosen["labels"], chosen["classes"]
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)   # only reachable from this PC
    url = f"http://localhost:{PORT}"
    print(f"Labelling page running at {url}  (Ctrl+C here to stop; progress is saved as you go)")
    print(f"Labels are saved to {LABELS}")
    webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped. Run the same command again to continue where you left off.")


if __name__ == "__main__":
    main()
