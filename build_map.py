"""Build the survey map from live KoboToolbox data.

Fetches submissions + the form definition (Lao labels) from the Kobo API,
then renders template.html into site/index.html as a self-contained map.

Environment variables:
  KOBO_TOKEN      KoboToolbox API token  (Account Settings -> Security -> API Key)
  KOBO_ASSET_UID  form id, e.g. aB3dE5fG7hJ9kL  (visible in the form URL)
  KOBO_SERVER     optional, default https://kf.kobotoolbox.org

Offline test mode:  python build_map.py --fake fake.json
  where fake.json = {"form": <asset json>, "records": [<submission>, ...]}
"""
import json
import os
import sys
import urllib.request
from pathlib import Path

HERE = Path(__file__).parent
SERVER = os.environ.get("KOBO_SERVER", "https://kf.kobotoolbox.org").rstrip("/")
TOKEN = os.environ.get("KOBO_TOKEN", "")
UID = os.environ.get("KOBO_ASSET_UID", "")
PASSWORD = os.environ.get("MAP_PASSWORD", "")  # when set, the published page is AES-encrypted

# account status (S2_Q7): label + dark-tone color
STATUS = {  # 1=has account, 2=opening in progress, 3=no account
    "1": {"label": "ມີບັນຊີ · Has account",                          "color": "#1b5e20"},
    "2": {"label": "ກໍາລັງດໍາເນີນການເປີດບັນຊີ · Opening in progress", "color": "#b8860b"},
    "3": {"label": "ບໍ່ມີບັນຊີ · No account",                         "color": "#b71c1c"},
}
FALLBACK_LABELS = {"S2_Q7": "ສະຖານະບັນຊີ · Account status",
                   "S2_Q9": "ເມືອງ · District",
                   "S2_Q10": "ບ້ານ · Village"}
# card order: (column, is multi-select?)
CARD_FIELDS = [("S2_Q1", False), ("S2_Q2", False), ("S2_Q3", False), ("S2_Q4", False),
               ("S2_Q5", False), ("S2_Q6", True),  ("S2_Q7", False), ("S2_Q8", True),
               ("S2_Q9", False), ("S2_Q10", False), ("S2_Q11", False)]
S3_COLS = ["S3_Q1", "S3_Q2", "S3_Q3", "S3_Q4", "S3_Q5"]


def api_get(url):
    req = urllib.request.Request(url, headers={"Authorization": f"Token {TOKEN}"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read().decode("utf-8"))


def fetch_form():
    return api_get(f"{SERVER}/api/v2/assets/{UID}.json")


def fetch_records():
    url = f"{SERVER}/api/v2/assets/{UID}/data.json?limit=10000"
    records = []
    while url:
        page = api_get(url)
        records += page.get("results", [])
        url = page.get("next")
    return records


def first_label(label):
    # Kobo stores labels as a list (one per language) or a plain string
    if isinstance(label, list):
        for x in label:
            if x:
                return str(x)
        return None
    return str(label) if label else None


def parse_form(asset):
    """asset json -> (question labels, question->list_name, choice labels)."""
    qlabels, qlist, choices = {}, {}, {}
    content = asset.get("content", {})
    for row in content.get("survey", []):
        name = row.get("name") or row.get("$autoname")
        if not name:
            continue
        lab = first_label(row.get("label"))
        if lab:
            qlabels[name] = lab
        ln = row.get("select_from_list_name")
        if ln:
            qlist[name] = ln
    for ch in content.get("choices", []):
        ln = ch.get("list_name")
        nm = ch.get("name") or ch.get("$autovalue")
        if ln and nm is not None:
            choices.setdefault(ln, {})[str(nm)] = first_label(ch.get("label")) or str(nm)
    return qlabels, qlist, choices


def norm(rec):
    # API record keys may include group prefixes ("Section_2/S2_Q1") -> strip them
    return {k.split("/")[-1]: v for k, v in rec.items()}


def fmt(v):
    if v is None:
        return None
    s = str(v).strip()
    return s if s and s.lower() != "nan" else None


def strip_num(label):
    # drop the form's own question number prefix (e.g. "2.1 ", "1.4 ") so the card
    # can renumber every row sequentially (1., 2., 3., ...)
    import re
    return re.sub(r"^\d+(\.\d+)*[.\s]*", "", str(label)).strip()


def latlon(rec):
    # use the "geopoint" question (the surveyed shop location, "lat lon alt prec").
    # NOTE: do NOT prefer Kobo's _geolocation — it mirrors the FIRST geo question in
    # the form, which here is start-geopoint (where the enumerator OPENED the form).
    raw = fmt(rec.get("geopoint"))
    if raw:
        parts = raw.split()
        if len(parts) >= 2:
            return float(parts[0]), float(parts[1])
    g = rec.get("_geolocation")
    if isinstance(g, list) and len(g) >= 2 and g[0] is not None and g[1] is not None:
        return float(g[0]), float(g[1])
    return None, None


def build(form_asset, raw_records):
    qlabels, qlist, choices = parse_form(form_asset)

    def qlabel(q):
        return qlabels.get(q) or FALLBACK_LABELS.get(q, q)

    def answer_text(rec, q, multi=False):
        raw, lookup = fmt(rec.get(q)), choices.get(qlist.get(q), {})
        if raw is None:
            txt = None
        elif multi or " " in raw:
            txt = ", ".join(lookup.get(c, c) for c in raw.split())
        else:
            txt = lookup.get(raw, raw)
        oth = fmt(rec.get(f"{q}_oth"))
        if oth:
            txt = f"{txt} — {oth}" if txt else oth
        return txt if txt is not None else "—"

    features, skipped = [], 0
    for raw in raw_records:
        rec = norm(raw)
        lat, lon = latlon(rec)
        if lat is None:
            skipped += 1
            continue
        st = fmt(rec.get("S2_Q7")) or "?"
        details = []
        for q, multi in CARD_FIELDS:
            ans = STATUS.get(st, {}).get("label", st) if q == "S2_Q7" else answer_text(rec, q, multi)
            details.append([qlabel(q), ans])
        s3 = sum(1 for q in S3_COLS if fmt(rec.get(q)) == "1")
        details.append(["ພາກທີ 3: ຄຳຕອບ \"ແມ່ນ\" · Section 3 \"Yes\" answers",
                        f"{s3} / {len(S3_COLS)}"])
        details.append([qlabel("S1_Q4"), answer_text(rec, "S1_Q4")])
        # renumber every row 1., 2., 3., ... (drop the form's own 2.1/1.4 prefixes)
        details = [[f"{i}. {strip_num(lbl)}", ans] for i, (lbl, ans) in enumerate(details, 1)]
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [lon, lat]},
            "properties": {
                "status":  st,
                "biztype": fmt(rec.get("S2_Q1")) or "?",
                "title":   fmt(rec.get("S2_Q2_oth")) or answer_text(rec, "S2_Q2"),
                "subtitle": f"ID {fmt(rec.get('_id'))} · Submitted {fmt(rec.get('_submission_time'))}",
                "details": details,
            },
        })

    # business-type filter entries: form order, only types present in the data
    biz_choices = choices.get(qlist.get("S2_Q1"), {})
    present = {f["properties"]["biztype"] for f in features}
    types = {c: lab for c, lab in biz_choices.items() if c in present}
    for c in sorted(present - set(types)):
        types[c] = c

    points_fc = {"type": "FeatureCollection", "features": features}
    print(f"records: {len(raw_records)} | points: {len(features)} | no coordinates: {skipped}")
    print("by status:", {s: sum(1 for f in features if f["properties"]["status"] == s) for s in STATUS})
    print("business types:", types)

    template = (HERE / "template.html").read_text(encoding="utf-8")
    districts = (HERE / "assets" / "districts_lpb.geojson").read_text(encoding="utf-8")
    def _asset(name):
        p = HERE / "assets" / name
        return p.read_text(encoding="utf-8") if p.exists() else '{"type":"FeatureCollection","features":[]}'

    logo_file = HERE / "assets" / "odf_logo_b64.txt"
    odf_logo = logo_file.read_text(encoding="utf-8").strip() if logo_file.exists() else ""
    html = (template
            .replace("__STATUS__",    json.dumps(STATUS, ensure_ascii=False))
            .replace("__TYPES__",     json.dumps(types, ensure_ascii=False))
            .replace("__POINTS__",    json.dumps(points_fc, ensure_ascii=False))
            .replace("__ROADS__",     _asset("roads_lpb.geojson"))
            .replace("__VILLAGES__",  _asset("villages_lpb.geojson"))
            .replace("__ODF_LOGO__",  odf_logo)
            .replace("__DISTRICTS__", districts))

    if PASSWORD:
        html = encrypt_page(html, PASSWORD)
        print("page encrypted with MAP_PASSWORD (AES-256-GCM, PBKDF2 600k)")
    else:
        print("WARNING: MAP_PASSWORD not set -> page published WITHOUT password protection")

    out = HERE / "site" / "index.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    print(f"saved: {out} ({out.stat().st_size / 1024:.0f} KB)")


# auto-lock: after 30 idle minutes, reload back to the lock screen (password must be re-entered)
IDLE_LOCK_JS = """<script>
(() => {
  const LIMIT_MS = 30 * 60 * 1000;
  let last = Date.now();
  const bump = () => { last = Date.now(); };
  ['mousemove', 'mousedown', 'keydown', 'touchstart', 'wheel', 'scroll'].forEach(ev =>
    addEventListener(ev, bump, { passive: true }));
  setInterval(() => {
    if (Date.now() - last >= LIMIT_MS) location.reload();
  }, 30000);
})();
</script>"""


def encrypt_page(html, password):
    """Wrap the map page in locker.html: AES-256-GCM payload + in-browser decrypt."""
    import base64
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

    html = html.replace("</body>", IDLE_LOCK_JS + "\n</body>")
    salt, iv = os.urandom(16), os.urandom(12)
    key = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt,
                     iterations=600_000).derive(password.encode("utf-8"))
    ct = AESGCM(key).encrypt(iv, html.encode("utf-8"), None)
    payload = {"salt": base64.b64encode(salt).decode(),
               "iv":   base64.b64encode(iv).decode(),
               "ct":   base64.b64encode(ct).decode()}
    locker = (HERE / "locker.html").read_text(encoding="utf-8")
    return locker.replace("__PAYLOAD__", json.dumps(payload))


def main():
    if len(sys.argv) >= 3 and sys.argv[1] == "--fake":
        fake = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
        build(fake["form"], fake["records"])
        return
    if not TOKEN or not UID:
        sys.exit("KOBO_TOKEN and KOBO_ASSET_UID environment variables are required")
    print(f"fetching form + data from {SERVER} (asset {UID}) ...")
    build(fetch_form(), fetch_records())


if __name__ == "__main__":
    main()
