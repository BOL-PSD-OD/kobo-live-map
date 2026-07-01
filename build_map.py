"""Build the survey map from live KoboToolbox data (NEW form).

Fetches submissions + the form definition (Lao labels) from the Kobo API,
then renders template.html into site/index.html as a self-contained map.

Account status is DERIVED (9 states) from the new form's payment questions
S3_Q7 / S3_Q12 / S3_Q15 — see derive_status() below, which mirrors
store_master/status.py and docs/store-survey-overview.md. (Kept inline so this
folder stays a self-contained repo for GitHub Actions.)

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

# Account status collapsed to 3 buckets (matches psp_export / dashboard interest):
# green = already in the domestic system, yellow = interested, red = not interested.
STATUS = {
    "using":        {"label": "ໃຊ້ແລ້ວ · ຢູ່ໃນລະບົບ · In system", "color": "#2e7d32",
                     "desc": "ໃຊ້ບໍລິການຮັບຊຳລະພາຍໃນແລ້ວ — ຢູ່ໃນລະບົບ · Already uses a domestic acquirer / QR"},
    "interested":   {"label": "ສົນໃຈ · Interested", "color": "#f9a825",
                     "desc": "ຍັງບໍ່ໃຊ້ບໍລິການພາຍໃນ ແຕ່ສົນໃຈ → ເປົ້າໝາຍ PSP · Not using domestic yet, interested"},
    "uninterested": {"label": "ບໍ່ສົນໃຈ · Not interested", "color": "#c62828",
                     "desc": "ຍັງບໍ່ໃຊ້ບໍລິການພາຍໃນ ແລະ ບໍ່ສົນໃຈ · Not using, not interested"},
}

# 9-state derive_status() key -> 3 display buckets (unknown -> None = hidden on the map).
STATUS3 = {
    "domestic": "using", "both_using": "using", "foreign_using": "using",
    "both_int": "interested", "foreign_int": "interested", "notool_int": "interested",
    "both_unint": "uninterested", "foreign_unint": "uninterested", "notool_unint": "uninterested",
}

FALLBACK_LABELS = {"S3.1_Q2": "ເມືອງ · District", "S3.1_Q3": "ບ້ານ · Village"}

# Business type (S3_Q1) -> Store ID prefix for non-catalog ("other") shops.
# Mirrors store_master/constants.py PREFIX_BY_BIZ (2026-06-27 form revision).
PREFIX_BY_BIZ = {"tour": "T", "hotel": "H", "restaurant": "R", "guesthouse": "G",
                 "karaoke": "K", "pub": "P", "nightclub": "N", "oth_biz": "O"}

# detail-card field order: (column, is multi-select?). 2026-07 form: Section 3.1
# leads with a License question (S3.1_Q1), so the free-text detail fields shift
# down one — district S3.1_Q2, village S3.1_Q3, owner S3.1_Q4, phone S3.1_Q6.
CARD_FIELDS = [("S3.1_Q4", False), ("S3.1_Q6", False),
               ("S3_Q1", False), ("S3.1_Q2", False), ("S3.1_Q3", False), ("S3_Q3", False),
               ("S3_Q4", True),  ("S3_Q6", True),  ("S3_Q5", True),  ("S3_Q11", True),
               ("S3_Q9", False), ("S3_Q12", False), ("S3_Q14", False)]
PSP_COLS = ["S3_Q7", "S3_Q8", "S3_Q10"]   # bank/PSP lists (Lao QR / merchant / foreign)
S2_COLS = ["S2_Q1", "S2_Q2", "S2_Q3"]     # awareness questions ("1" = heard before)


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


def fetch_from_sheet():
    """Read (form, records) from the sync Google Sheet (_form/_raw).
    Prefers the user's OAuth creds (service account may be deleted / has no quota);
    falls back to the service account. Lets the map keep working after Kobo deletes."""
    import gspread
    sid = os.environ["SHEET_ID"]
    client_id = os.environ.get("GOOGLE_OAUTH_CLIENT_ID")
    if client_id:
        from google.oauth2.credentials import Credentials as UserCredentials
        creds = UserCredentials(
            None, refresh_token=os.environ["GOOGLE_OAUTH_REFRESH_TOKEN"],
            client_id=client_id, client_secret=os.environ["GOOGLE_OAUTH_CLIENT_SECRET"],
            token_uri="https://oauth2.googleapis.com/token",
            scopes=["https://www.googleapis.com/auth/drive"])
    else:
        from google.oauth2.service_account import Credentials
        creds = Credentials.from_service_account_info(
            json.loads(os.environ["GOOGLE_SA_JSON"]),
            scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"])
    sh = gspread.authorize(creds).open_by_key(sid)
    raw_rows = sh.worksheet("_raw").get_all_records()
    form_chunks = sh.worksheet("_form").col_values(1)[1:]   # column A, skip "form_json" header
    records = [json.loads(r["raw_json"]) for r in raw_rows if r.get("raw_json")]
    form_json = "".join(str(c) for c in form_chunks)
    form = json.loads(form_json) if form_json else {}
    return form, records


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
    # API record keys may include group prefixes ("Section_3/S3_Q1") -> strip them
    return {k.split("/")[-1]: v for k, v in rec.items()}


def fmt(v):
    if v is None:
        return None
    s = str(v).strip()
    return s if s and s.lower() != "nan" else None


def codeset(rec, q):
    """select_multiple value -> set of codes (Kobo stores them space-separated)."""
    raw = fmt(rec.get(q))
    return set(raw.split()) if raw else set()


def derive_status(acquirer, qr, use_domestic, interested):
    """9-state status = acquirer x use_domestic x interested (qr is data only).
    acquirer codes: '1' domestic, '2' foreign, '3' no payment tool.
    Mirrors store_master/status.py (kept inline to keep this repo self-contained)."""
    acquirer = set(acquirer or [])
    dom = "1" in acquirer
    foreign = "2" in acquirer or "0" in acquirer   # "2" new form / "0" legacy form
    notool = "3" in acquirer
    if dom and foreign:
        if use_domestic == "1":
            return "both_using"
        return "both_int" if interested == "1" else "both_unint"
    if foreign:
        if use_domestic == "1":
            return "foreign_using"
        return "foreign_int" if interested == "1" else "foreign_unint"
    if dom:
        return "domestic"
    if notool:
        return "notool_int" if interested == "1" else "notool_unint"
    return "unknown"


def strip_num(label):
    # drop the form's own question number prefix (e.g. "3.1 ", "2.7 ") so the card
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


def assign_store_ids(raw_records, choices):
    """Map each submission key (_uuid, or _id fallback) -> Store ID.

    Catalog shops keep their S3_Q2 code (e.g. T001 / H010); 'other' shops get
    <prefix><nnn> by business type, continuing after the catalog's max number.
    Deterministic — assigned in submission order so IDs stay stable across the
    30-min rebuilds (mirrors the store_master / Code.gs engine)."""
    import re
    counters = {p: 0 for p in PREFIX_BY_BIZ.values()}
    for code in choices.get("shop_name", {}):              # seed from catalog max
        m = re.match(r"^([A-Z])(\d+)$", str(code))
        if m and m.group(1) in counters:
            counters[m.group(1)] = max(counters[m.group(1)], int(m.group(2)))
    ordered = sorted(raw_records, key=lambda r: (fmt(norm(r).get("_submission_time")) or "",
                                                 norm(r).get("_id") or 0))
    ids = {}
    for raw in ordered:
        rec = norm(raw)
        key = fmt(rec.get("_uuid")) or fmt(rec.get("_id"))
        code = fmt(rec.get("S3_Q2"))
        if code and code != "other_shop":
            ids[key] = code
        else:
            pfx = PREFIX_BY_BIZ.get(fmt(rec.get("S3_Q1")), "O")
            counters[pfx] += 1
            ids[key] = f"{pfx}{counters[pfx]:03d}"
    return ids


def build(form_asset, raw_records):
    qlabels, qlist, choices = parse_form(form_asset)

    # Legacy data: the old form used acquirer "0" for foreign (the deployed form
    # now uses "2"). Alias "0" to the foreign label so old submissions render the
    # text instead of a bare "0" on the card.
    acq = choices.get("acquirer")
    if acq and "0" not in acq:
        acq["0"] = acq.get("2", "ຮັບຊຳລະຕ່າງປະເທດ · Foreign")

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

    def psp_text(rec):
        lookup = choices.get("psp", {})
        seen, out = set(), []
        for q in PSP_COLS:
            raw = fmt(rec.get(q))
            if not raw:
                continue
            for c in raw.split():
                if c not in seen:
                    seen.add(c)
                    out.append(lookup.get(c, c))
            oth = fmt(rec.get(f"{q}_oth"))
            if oth and oth not in out:
                out.append(oth)
        return ", ".join(out) if out else "—"

    store_ids = assign_store_ids(raw_records, choices)

    features, skipped, hidden = [], 0, 0
    for raw in raw_records:
        rec = norm(raw)
        lat, lon = latlon(rec)
        if lat is None:
            skipped += 1
            continue

        status = STATUS3.get(derive_status(
            codeset(rec, "S3_Q4"), codeset(rec, "S3_Q6"),
            fmt(rec.get("S3_Q9")), fmt(rec.get("S3_Q12"))))
        if status is None:          # incomplete data -> not classifiable -> hidden per design
            hidden += 1
            continue

        details = []
        for q, multi in CARD_FIELDS:
            ans = answer_text(rec, q, multi)
            details.append([qlabel(q), ans])
        details.append(["ທະນາຄານ/ຜູ້ໃຫ້ບໍລິການ · Bank / PSP", psp_text(rec)])
        s2 = sum(1 for q in S2_COLS if fmt(rec.get(q)) == "1")
        details.append(["ການຮັບຮູ້ · Awareness",
                        f"{s2} / {len(S2_COLS)}"])
        # renumber every row 1., 2., 3., ... (drop the form's own 3.1/2.7 prefixes)
        details = [[f"{i}. {strip_num(lbl)}", ans] for i, (lbl, ans) in enumerate(details, 1)]

        key = fmt(rec.get("_uuid")) or fmt(rec.get("_id"))
        store_id = store_ids.get(key, "—")
        title = fmt(rec.get("S3_Q2_oth")) or answer_text(rec, "S3_Q2")
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [lon, lat]},
            "properties": {
                "status":  status,
                "biztype": fmt(rec.get("S3_Q1")) or "?",
                "store_id": store_id,
                "title":   title,
                "subtitle": f"ລະຫັດ · ID {store_id} · {fmt(rec.get('_submission_time')) or ''}",
                "details": details,
            },
        })

    # business-type filter entries: form order, only types present in the data
    biz_choices = choices.get(qlist.get("S3_Q1"), {})
    present = {f["properties"]["biztype"] for f in features}
    types = {c: lab for c, lab in biz_choices.items() if c in present}
    for c in sorted(present - set(types)):
        types[c] = c

    points_fc = {"type": "FeatureCollection", "features": features}
    print(f"records: {len(raw_records)} | points: {len(features)} | no coordinates: {skipped} | hidden(unclassified): {hidden}")
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
    if not os.environ.get("SHEET_ID") or not (
            os.environ.get("GOOGLE_OAUTH_CLIENT_ID") or os.environ.get("GOOGLE_SA_JSON")):
        sys.exit("SHEET_ID and (GOOGLE_OAUTH_* or GOOGLE_SA_JSON) environment variables are required")
    print("fetching form + data from Google Sheet (_raw / _form) ...")
    build(*fetch_from_sheet())   # (form, records) — fetch_form()/fetch_records() kept as dormant fallback


if __name__ == "__main__":
    main()
