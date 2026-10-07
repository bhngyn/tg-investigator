"""
Cross-section overlap and community overview for collected comment sections.

Read-only queries over what the comments collector already stored. Nothing here
talks to Telegram. Phone countries and languages are worked out on this laptop
with the small, explainable rules below (no external libraries or services).
"""

import re
import unicodedata
from collections import Counter, defaultdict
from contextlib import nullcontext
from datetime import datetime

# ---- phone numbers → country ----------------------------------------------------
# ITU country calling codes. Phones are stored as Telegram returns them: digits, no "+".
CALLING_CODES = {
    "1": ("US", "United States"),
    "20": ("EG", "Egypt"), "211": ("SS", "South Sudan"), "212": ("MA", "Morocco"), "213": ("DZ", "Algeria"),
    "216": ("TN", "Tunisia"), "218": ("LY", "Libya"), "220": ("GM", "Gambia"), "221": ("SN", "Senegal"),
    "222": ("MR", "Mauritania"), "223": ("ML", "Mali"), "224": ("GN", "Guinea"), "225": ("CI", "Côte d'Ivoire"),
    "226": ("BF", "Burkina Faso"), "227": ("NE", "Niger"), "228": ("TG", "Togo"), "229": ("BJ", "Benin"),
    "230": ("MU", "Mauritius"), "231": ("LR", "Liberia"), "232": ("SL", "Sierra Leone"), "233": ("GH", "Ghana"),
    "234": ("NG", "Nigeria"), "235": ("TD", "Chad"), "236": ("CF", "Central African Republic"),
    "237": ("CM", "Cameroon"), "238": ("CV", "Cape Verde"), "239": ("ST", "São Tomé and Príncipe"),
    "240": ("GQ", "Equatorial Guinea"), "241": ("GA", "Gabon"), "242": ("CG", "Congo"),
    "243": ("CD", "DR Congo"), "244": ("AO", "Angola"), "245": ("GW", "Guinea-Bissau"),
    "246": ("IO", "Diego Garcia"), "248": ("SC", "Seychelles"), "249": ("SD", "Sudan"), "250": ("RW", "Rwanda"),
    "251": ("ET", "Ethiopia"), "252": ("SO", "Somalia"), "253": ("DJ", "Djibouti"), "254": ("KE", "Kenya"),
    "255": ("TZ", "Tanzania"), "256": ("UG", "Uganda"), "257": ("BI", "Burundi"), "258": ("MZ", "Mozambique"),
    "260": ("ZM", "Zambia"), "261": ("MG", "Madagascar"), "262": ("RE", "Réunion / Mayotte"),
    "263": ("ZW", "Zimbabwe"), "264": ("NA", "Namibia"), "265": ("MW", "Malawi"), "266": ("LS", "Lesotho"),
    "267": ("BW", "Botswana"), "268": ("SZ", "Eswatini"), "269": ("KM", "Comoros"), "27": ("ZA", "South Africa"),
    "290": ("SH", "Saint Helena"), "291": ("ER", "Eritrea"), "297": ("AW", "Aruba"), "298": ("FO", "Faroe Islands"),
    "299": ("GL", "Greenland"),
    "30": ("GR", "Greece"), "31": ("NL", "Netherlands"), "32": ("BE", "Belgium"), "33": ("FR", "France"),
    "34": ("ES", "Spain"), "350": ("GI", "Gibraltar"), "351": ("PT", "Portugal"), "352": ("LU", "Luxembourg"),
    "353": ("IE", "Ireland"), "354": ("IS", "Iceland"), "355": ("AL", "Albania"), "356": ("MT", "Malta"),
    "357": ("CY", "Cyprus"), "358": ("FI", "Finland"), "359": ("BG", "Bulgaria"), "36": ("HU", "Hungary"),
    "370": ("LT", "Lithuania"), "371": ("LV", "Latvia"), "372": ("EE", "Estonia"), "373": ("MD", "Moldova"),
    "374": ("AM", "Armenia"), "375": ("BY", "Belarus"), "376": ("AD", "Andorra"), "377": ("MC", "Monaco"),
    "378": ("SM", "San Marino"), "380": ("UA", "Ukraine"), "381": ("RS", "Serbia"), "382": ("ME", "Montenegro"),
    "383": ("XK", "Kosovo"), "385": ("HR", "Croatia"), "386": ("SI", "Slovenia"),
    "387": ("BA", "Bosnia and Herzegovina"), "389": ("MK", "North Macedonia"), "39": ("IT", "Italy"),
    "40": ("RO", "Romania"), "41": ("CH", "Switzerland"), "420": ("CZ", "Czechia"), "421": ("SK", "Slovakia"),
    "423": ("LI", "Liechtenstein"), "43": ("AT", "Austria"), "44": ("GB", "United Kingdom"), "45": ("DK", "Denmark"),
    "46": ("SE", "Sweden"), "47": ("NO", "Norway"), "48": ("PL", "Poland"), "49": ("DE", "Germany"),
    "500": ("FK", "Falkland Islands"), "501": ("BZ", "Belize"), "502": ("GT", "Guatemala"),
    "503": ("SV", "El Salvador"), "504": ("HN", "Honduras"), "505": ("NI", "Nicaragua"), "506": ("CR", "Costa Rica"),
    "507": ("PA", "Panama"), "509": ("HT", "Haiti"), "51": ("PE", "Peru"), "52": ("MX", "Mexico"), "53": ("CU", "Cuba"),
    "54": ("AR", "Argentina"), "55": ("BR", "Brazil"), "56": ("CL", "Chile"), "57": ("CO", "Colombia"),
    "58": ("VE", "Venezuela"), "590": ("GP", "Guadeloupe"), "591": ("BO", "Bolivia"), "592": ("GY", "Guyana"),
    "593": ("EC", "Ecuador"), "594": ("GF", "French Guiana"), "595": ("PY", "Paraguay"), "596": ("MQ", "Martinique"),
    "597": ("SR", "Suriname"), "598": ("UY", "Uruguay"), "599": ("CW", "Curaçao / Caribbean Netherlands"),
    "60": ("MY", "Malaysia"), "61": ("AU", "Australia"), "62": ("ID", "Indonesia"), "63": ("PH", "Philippines"),
    "64": ("NZ", "New Zealand"), "65": ("SG", "Singapore"), "66": ("TH", "Thailand"), "670": ("TL", "Timor-Leste"),
    "673": ("BN", "Brunei"), "674": ("NR", "Nauru"), "675": ("PG", "Papua New Guinea"), "676": ("TO", "Tonga"),
    "677": ("SB", "Solomon Islands"), "678": ("VU", "Vanuatu"), "679": ("FJ", "Fiji"), "680": ("PW", "Palau"),
    "685": ("WS", "Samoa"), "686": ("KI", "Kiribati"), "687": ("NC", "New Caledonia"),
    "689": ("PF", "French Polynesia"), "691": ("FM", "Micronesia"), "692": ("MH", "Marshall Islands"),
    "7": ("RU", "Russia"),
    "81": ("JP", "Japan"), "82": ("KR", "South Korea"), "84": ("VN", "Vietnam"), "850": ("KP", "North Korea"),
    "852": ("HK", "Hong Kong"), "853": ("MO", "Macau"), "855": ("KH", "Cambodia"), "856": ("LA", "Laos"),
    "86": ("CN", "China"), "880": ("BD", "Bangladesh"), "886": ("TW", "Taiwan"), "888": ("XX", "Anonymous number (Fragment)"),
    "90": ("TR", "Türkiye"), "91": ("IN", "India"), "92": ("PK", "Pakistan"), "93": ("AF", "Afghanistan"),
    "94": ("LK", "Sri Lanka"), "95": ("MM", "Myanmar"), "960": ("MV", "Maldives"), "961": ("LB", "Lebanon"),
    "962": ("JO", "Jordan"), "963": ("SY", "Syria"), "964": ("IQ", "Iraq"), "965": ("KW", "Kuwait"),
    "966": ("SA", "Saudi Arabia"), "967": ("YE", "Yemen"), "968": ("OM", "Oman"), "970": ("PS", "Palestine"),
    "971": ("AE", "United Arab Emirates"), "972": ("IL", "Israel"), "973": ("BH", "Bahrain"), "974": ("QA", "Qatar"),
    "975": ("BT", "Bhutan"), "976": ("MN", "Mongolia"), "977": ("NP", "Nepal"), "98": ("IR", "Iran"),
    "992": ("TJ", "Tajikistan"), "993": ("TM", "Turkmenistan"), "994": ("AZ", "Azerbaijan"), "995": ("GE", "Georgia"),
    "996": ("KG", "Kyrgyzstan"), "998": ("UZ", "Uzbekistan"),
}
# +1 is shared by the US, Canada and the Caribbean; the area code decides.
NANP_OTHER = {
    **{a: ("CA", "Canada") for a in (
        "204 226 236 249 250 257 263 289 306 343 354 365 367 368 382 403 416 418 428 431 437 438 450 460 468 "
        "474 506 514 519 548 579 581 584 587 604 613 639 647 672 683 705 709 742 753 778 780 782 807 819 825 "
        "867 873 879 902 905").split()},
    "242": ("BS", "Bahamas"), "246": ("BB", "Barbados"), "264": ("AI", "Anguilla"), "268": ("AG", "Antigua and Barbuda"),
    "284": ("VG", "British Virgin Islands"), "340": ("VI", "US Virgin Islands"), "345": ("KY", "Cayman Islands"),
    "441": ("BM", "Bermuda"), "473": ("GD", "Grenada"), "649": ("TC", "Turks and Caicos"), "658": ("JM", "Jamaica"),
    "876": ("JM", "Jamaica"), "664": ("MS", "Montserrat"), "670": ("MP", "Northern Mariana Islands"),
    "671": ("GU", "Guam"), "684": ("AS", "American Samoa"), "721": ("SX", "Sint Maarten"), "758": ("LC", "Saint Lucia"),
    "767": ("DM", "Dominica"), "784": ("VC", "Saint Vincent"), "787": ("PR", "Puerto Rico"), "939": ("PR", "Puerto Rico"),
    "809": ("DO", "Dominican Republic"), "829": ("DO", "Dominican Republic"), "849": ("DO", "Dominican Republic"),
    "868": ("TT", "Trinidad and Tobago"), "869": ("KN", "Saint Kitts and Nevis"),
}


def phone_country(phone):
    """(calling code, ISO code, country) for a stored phone number, or None."""
    digits = re.sub(r"\D", "", phone or "")
    if not digits:
        return None
    if digits.startswith("1"):
        iso, name = NANP_OTHER.get(digits[1:4], CALLING_CODES["1"])
        return "1", iso, name
    if digits.startswith("7"):
        # +7 is shared: Kazakhstan uses 76x/77x, the rest is Russia
        return ("7", "KZ", "Kazakhstan") if digits[1:2] in ("6", "7") else ("7", "RU", "Russia")
    for n in (3, 2, 1):
        hit = CALLING_CODES.get(digits[:n])
        if hit:
            return digits[:n], hit[0], hit[1]
    return None


# ---- comment text → language ---------------------------------------------------
# Script first, then a few distinguishing letters or common words. This is a
# heuristic: it's good at "Russian vs Ukrainian vs Arabic vs English" in bulk and
# says "unsure" rather than guessing on short or mixed text.

LANG_NAMES = {
    "ru": "Russian", "uk": "Ukrainian", "be": "Belarusian", "cyr": "Cyrillic (other / unsure)",
    "ar": "Arabic", "fa": "Persian", "ur": "Urdu", "en": "English", "fr": "French", "es": "Spanish",
    "pt": "Portuguese", "de": "German", "it": "Italian", "tr": "Turkish", "latn": "Latin script (other / unsure)",
    "he": "Hebrew", "am": "Amharic / Tigrinya (Ethiopic)", "zh": "Chinese", "ja": "Japanese", "ko": "Korean",
    "hy": "Armenian", "ka": "Georgian", "other": "Other script", "none": "No text (emoji, media or links only)",
}
_STOP = {
    "en": "the and is are you to of in that it for on with this was not have be they what we but all",
    "fr": "le la les des est et une un pas que qui pour dans ce il elle sur avec sont nous vous mais",
    "es": "el la los las es y que de una por con para no se lo su pero más como está son muy",
    "pt": "o a os as é e que de um uma não para com por se do da mas são você muito também",
    "de": "der die das und ist nicht ein eine ich sie es mit auf den dem zu auch sind wir aber",
    "it": "il la che di è e non un una per con sono ma anche questo si gli le mi ci",
    "tr": "ve bir bu da de ne için çok ama gibi var yok ben sen o mi mı değil daha",
}
_STOP = {k: set(v.split()) for k, v in _STOP.items()}
_URL = re.compile(r"https?://\S+|www\.\S+|t\.me/\S+|@\w+|#\w+")


def _script(ch):
    o = ord(ch)
    if 0x0400 <= o <= 0x052F:
        return "cyr"
    if 0x0600 <= o <= 0x06FF or 0x0750 <= o <= 0x077F or 0x08A0 <= o <= 0x08FF or 0xFB50 <= o <= 0xFEFF:
        return "arab"
    if o < 0x250 and ch.isalpha():
        return "latn"
    if 0x0590 <= o <= 0x05FF:
        return "he"
    if 0x1200 <= o <= 0x139F:
        return "am"
    if 0x3040 <= o <= 0x30FF:
        return "ja"
    if 0xAC00 <= o <= 0xD7AF:
        return "ko"
    if 0x4E00 <= o <= 0x9FFF:
        return "zh"
    if 0x0530 <= o <= 0x058F:
        return "hy"
    if 0x10A0 <= o <= 0x10FF:
        return "ka"
    return "other" if ch.isalpha() else None


def detect_language(text):
    """Language code (see LANG_NAMES) for a comment, using script and simple markers."""
    t = _URL.sub(" ", unicodedata.normalize("NFC", text or "")).lower()
    scripts = Counter(s for s in map(_script, t) if s)
    if sum(scripts.values()) < 3:
        return "none"
    script, _ = scripts.most_common(1)[0]
    if script == "cyr":
        uk = sum(t.count(c) for c in "іїєґ")
        ru = sum(t.count(c) for c in "ыэъё")
        if "ў" in t and not ru:
            return "be"
        if uk > ru:
            return "uk"
        if ru or scripts["cyr"] >= 12:
            return "ru"    # long Cyrillic text without Ukrainian letters is almost always Russian
        return "cyr"
    if script == "arab":
        if any(c in t for c in "ٹڈڑںے"):
            return "ur"
        if any(c in t for c in "پچژگ"):
            return "fa"
        return "ar"
    if script == "latn":
        words = re.findall(r"[^\W\d_]+", t)
        if any(c in t for c in "ğış"):
            return "tr"
        scores = {k: sum(w in s for w in words) for k, s in _STOP.items()}
        best = max(scores, key=scores.get)
        if scores[best] >= 2 or (scores[best] == 1 and len(words) <= 4):
            return best
        return "latn"
    return script


# ---- queries ---------------------------------------------------------------------

def _q(con, sql, args=(), lock=None):
    # lock per query, so a big overview doesn't hold up collection jobs while Python crunches the rows
    with lock or nullcontext():
        return [dict(r) for r in con.execute(sql, args).fetchall()]


def _name(r):
    return " ".join(x for x in (r.get("first_name"), r.get("last_name")) if x) or None


def _case_scoped(sql, case_id):
    """Run a query against only the comment sections of one case's channels."""
    if not case_id:
        return sql
    ch = f"(SELECT channel FROM case_channels WHERE case_id = {int(case_id)})"
    sql = re.sub(r"\bFROM comments\b", "FROM cm", sql)
    sql = re.sub(r"\b(FROM|JOIN) comment_groups\b", r"\1 cg", sql)
    return (f"WITH cm AS (SELECT * FROM comments WHERE channel IN {ch}), "
            f"cg AS (SELECT * FROM comment_groups WHERE channel IN {ch}) " + sql)


def overlap(con, lock=None, case_id=None):
    """Accounts, channel identities and forward sources seen in more than one comment section.
    With case_id, only the comment sections of that case's channels count."""
    q = lambda sql, args=(): _q(con, _case_scoped(sql, case_id), args, lock)
    groups = q("""SELECT g.group_id, g.channel, g.title,
                          (SELECT COUNT(DISTINCT sender_id) FROM comments c WHERE c.group_id = g.group_id
                             AND c.is_mirror = 0 AND c.sender_type = 'user') commenters
                        FROM comment_groups g ORDER BY g.channel""")
    admins = defaultdict(list)
    for a in q("""SELECT a.user_id, a.group_id, a.role, a.rank, a.removed_detected, g.channel
                        FROM group_admins a JOIN comment_groups g ON g.group_id = a.group_id"""):
        admins[a["user_id"]].append(a)

    per_user = q("""SELECT c.sender_id user_id, c.group_id, c.channel, COUNT(*) n,
                            SUM(c.deleted_detected IS NOT NULL) deleted, MIN(c.date) first, MAX(c.date) last
                          FROM comments c WHERE c.is_mirror = 0 AND c.sender_type = 'user'
                            AND c.sender_id IN (SELECT sender_id FROM comments WHERE is_mirror = 0
                              AND sender_type = 'user' GROUP BY sender_id HAVING COUNT(DISTINCT group_id) > 1
                            UNION SELECT user_id FROM group_admins)
                          GROUP BY c.sender_id, c.group_id ORDER BY n DESC""")
    by_user = defaultdict(list)
    for r in per_user:
        by_user[r["user_id"]].append(r)
    users = {u["user_id"]: u for u in q(f"""SELECT user_id, username, first_name, last_name, phone, bot, deleted
        FROM tg_users WHERE user_id IN ({','.join('?' * len(by_user))})""", list(by_user))} if by_user else {}

    def person(uid):
        u = users.get(uid, {})
        pc = phone_country(u.get("phone"))
        return {"user_id": uid, "name": _name(u), "username": u.get("username"), "phone": u.get("phone"),
                "phone_country": pc[2] if pc else None, "bot": u.get("bot"), "deleted": u.get("deleted")}

    multi = []
    for uid, rows in by_user.items():
        if len({r["group_id"] for r in rows}) < 2:
            continue
        multi.append({**person(uid), "sections": rows, "total": sum(r["n"] for r in rows),
                      "first": min(r["first"] for r in rows), "last": max(r["last"] for r in rows),
                      "admin_of": [{k: a[k] for k in ("channel", "role", "rank", "removed_detected")}
                                   for a in admins.get(uid, [])]})
    multi.sort(key=lambda m: (-len(m["sections"]), -m["total"]))

    # the flagship view: someone who moderates one comment section and comments in another
    cross_admin = []
    for uid, roles in admins.items():
        admin_groups = {a["group_id"] for a in roles}
        elsewhere = [r for r in by_user.get(uid, []) if r["group_id"] not in admin_groups]
        if elsewhere:
            cross_admin.append({**person(uid), "admin_of": roles, "comments_elsewhere": elsewhere,
                                "total_elsewhere": sum(r["n"] for r in elsewhere)})
    cross_admin.sort(key=lambda m: -m["total_elsewhere"])

    channel_ids = q("""SELECT c.sender_id chat_id, t.username, t.title, COUNT(*) n,
                               COUNT(DISTINCT c.group_id) sections, GROUP_CONCAT(DISTINCT c.channel) channels,
                               MIN(c.date) first, MAX(c.date) last
                             FROM comments c LEFT JOIN tg_chats t ON t.chat_id = c.sender_id
                             WHERE c.is_mirror = 0 AND c.sender_type = 'channel'
                             GROUP BY c.sender_id HAVING sections > 1 ORDER BY sections DESC, n DESC""")
    fwd_sources = q("""SELECT c.fwd_type, c.fwd_id, MAX(c.fwd_username) username, MAX(c.fwd_name) name,
                               COUNT(*) n, COUNT(DISTINCT c.group_id) sections, GROUP_CONCAT(DISTINCT c.channel) channels,
                               COUNT(DISTINCT CASE WHEN c.sender_type = 'user' THEN c.sender_id END) forwarders,
                               MIN(c.date) first, MAX(c.date) last
                             FROM comments c WHERE c.is_mirror = 0 AND c.fwd_type IS NOT NULL
                             GROUP BY c.fwd_type, COALESCE(c.fwd_id, c.fwd_name)
                             HAVING sections > 1 ORDER BY sections DESC, n DESC LIMIT 500""")

    shared = q("""SELECT a.group_id g1, b.group_id g2, COUNT(*) shared FROM
                          (SELECT DISTINCT group_id, sender_id FROM comments WHERE is_mirror = 0 AND sender_type = 'user') a
                          JOIN (SELECT DISTINCT group_id, sender_id FROM comments WHERE is_mirror = 0 AND sender_type = 'user') b
                            ON a.sender_id = b.sender_id AND a.group_id < b.group_id
                        GROUP BY a.group_id, b.group_id""")
    return {"groups": groups, "accounts": multi, "cross_admins": cross_admin,
            "channel_identities": channel_ids, "forward_sources": fwd_sources, "matrix": shared}


def community(con, channel=None, lock=None, case_id=None):
    """Who comments in one comment section (or all of them): phones, languages, activity times."""
    q = lambda sql, args=(): _q(con, sql, args, lock)
    where, args = "c.is_mirror = 0", []
    if channel:
        where += " AND c.channel = ?"
        args.append(channel)
    if case_id:
        where += " AND c.channel IN (SELECT channel FROM case_channels WHERE case_id = ?)"
        args.append(int(case_id))
    rows = q(f"""SELECT c.id, c.date, c.text, c.sender_type, c.sender_id, c.deleted_detected, c.fwd_type
                       FROM comments c WHERE {where}""", args)
    senders = Counter(r["sender_id"] for r in rows if r["sender_type"] == "user")
    users = {}
    ids = list(senders)
    for i in range(0, len(ids), 900):
        chunk = ids[i:i + 900]
        for u in q(f"SELECT * FROM tg_users WHERE user_id IN ({','.join('?' * len(chunk))})", chunk):
            users[u["user_id"]] = u
    admin_ids = {r["user_id"] for r in q("SELECT a.user_id FROM group_admins a JOIN comment_groups g "
                                               "ON g.group_id = a.group_id" + (" WHERE g.channel = ?" if channel else ""),
                                          [channel] if channel else [])}
    sections = defaultdict(set)
    if ids:
        for r in q("SELECT DISTINCT sender_id, channel FROM comments WHERE is_mirror = 0 AND sender_type = 'user'"):
            if r["sender_id"] in senders:
                sections[r["sender_id"]].add(r["channel"])

    # phones
    countries = {}
    for uid in senders:
        pc = phone_country(users.get(uid, {}).get("phone"))
        if not pc:
            continue
        c = countries.setdefault(pc[1], {"code": pc[0], "iso": pc[1], "country": pc[2], "accounts": []})
        u = users[uid]
        c["accounts"].append({"user_id": uid, "name": _name(u), "username": u.get("username"),
                              "phone": u.get("phone"), "comments": senders[uid]})
    countries = sorted(countries.values(), key=lambda c: -len(c["accounts"]))

    # languages, and activity by UTC hour / weekday
    langs = {}
    hours = [0] * 24
    weekday_hour = [[0] * 24 for _ in range(7)]
    per_day = Counter()
    for r in rows:
        lg = detect_language(r["text"])
        L = langs.setdefault(lg, {"lang": lg, "name": LANG_NAMES.get(lg, lg), "comments": 0, "accounts": set(),
                                  "samples": []})
        L["comments"] += 1
        if r["sender_type"] == "user":
            L["accounts"].add(r["sender_id"])
        if len(L["samples"]) < 5 and lg != "none":
            L["samples"].append({"id": r["id"], "text": (r["text"] or "")[:160]})
        if r["date"]:
            try:
                d = datetime.fromisoformat(r["date"])
            except ValueError:
                continue
            hours[d.hour] += 1
            weekday_hour[d.weekday()][d.hour] += 1
            per_day[r["date"][:10]] += 1
    langs = sorted(({**L, "accounts": len(L["accounts"])} for L in langs.values()), key=lambda L: -L["comments"])

    u_rows = [users.get(uid, {}) for uid in senders]
    counts = sorted(senders.values(), reverse=True)
    top_share = sum(counts[:10]) / sum(counts) if counts else 0
    summary = {
        "comments": len(rows),
        "by_accounts": sum(senders.values()),
        "as_channel": sum(r["sender_type"] == "channel" for r in rows),
        "anonymous_admin": sum(r["sender_type"] == "group" for r in rows),
        "forwards": sum(bool(r["fwd_type"]) for r in rows),
        "deleted": sum(bool(r["deleted_detected"]) for r in rows),
        "accounts": len(senders),
        "with_phone": sum(bool(u.get("phone")) for u in u_rows),
        "with_username": sum(bool(u.get("username")) for u in u_rows),
        "premium": sum(bool(u.get("premium")) for u in u_rows),
        "bots": sum(bool(u.get("bot")) for u in u_rows),
        "deleted_accounts": sum(bool(u.get("deleted")) for u in u_rows),
        "one_comment": sum(n == 1 for n in counts),
        "top10_share": round(top_share, 3),
        "in_other_sections": sum(len(sections[uid]) > 1 for uid in senders),
    }
    top = []
    for uid, n in senders.most_common(30):
        u = users.get(uid, {})
        pc = phone_country(u.get("phone"))
        top.append({"user_id": uid, "name": _name(u), "username": u.get("username"), "phone": u.get("phone"),
                    "phone_country": pc[2] if pc else None, "bot": u.get("bot"), "premium": u.get("premium"),
                    "comments": n, "admin": uid in admin_ids, "sections": len(sections[uid])})
    return {"channel": channel, "case": case_id, "summary": summary, "countries": countries, "languages": langs,
            "hours_utc": hours, "weekday_hour_utc": weekday_hour,
            "per_day": [{"d": d, "n": n} for d, n in sorted(per_day.items())], "top": top}
