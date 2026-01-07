#!/usr/bin/env python3
import hashlib
import json
import os
import re
import smtplib
from email.message import EmailMessage
from typing import List, Dict, Optional, Tuple
from datetime import datetime

import requests
from bs4 import BeautifulSoup

TEAM_TID = "47"
TEAM_NAME = "AMOR GSBC 1"
COMP_ID = "39A69CCC-55A7-47C2-A19C-E41728508953"

URL_OVERVIEW  = f"https://badmintonnederland.toernooi.nl/sport/teammatches.aspx?id={COMP_ID}&tid={TEAM_TID}"
URL_STANDINGS = f"https://badmintonnederland.toernooi.nl/sport/teamstandings.aspx?id={COMP_ID}&tid={TEAM_TID}"

def slugify_team_name(name: str) -> str:
    """
    Zet teamnaam om naar veilige bestandsnaam:
    'AMOR GSBC 1' -> 'amor_gsbc_1'
    """
    s = name.lower().strip()
    s = re.sub(r"[^a-z0-9]+", "_", s)
    return s.strip("_")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(
    BASE_DIR,
    f"state_{slugify_team_name(TEAM_NAME)}.json")
TEMPLATE_FILE = os.path.join(BASE_DIR, "email_template.html")

# ---- Mail ----
SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587
SMTP_USER = "${SMTP_USER}"
SMTP_PASS = "${SMTP_PASS}"   # app password
MAIL_TO   = "${MAIL_TO}"

# ---- Telegram ----
TELEGRAM_BOT_TOKEN = "${TELEGRAM_BOT_TOKEN}"
TELEGRAM_CHAT_ID = "${TELEGRAM_CHAT_ID}"  # mag string of int
TELEGRAM_PARSE_MODE = "HTML"  # we gebruiken HTML formatting


def load_state() -> dict:
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def save_state(state: dict) -> None:
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)

def send_email(subject: str, body_text: str, body_html: Optional[str] = None) -> None:
    msg = EmailMessage()
    msg["From"] = f"🏸 {TEAM_NAME} Update <{SMTP_USER}>"
    msg["To"] = MAIL_TO
    msg["Subject"] = subject

    # Plain text fallback
    msg.set_content(body_text)

    # HTML versie
    if body_html:
        msg.add_alternative(body_html, subtype="html")

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as s:
        s.starttls()
        s.login(SMTP_USER, SMTP_PASS)
        s.send_message(msg)
        
def send_telegram(message_html: str) -> None:
    """
    Stuurt een Telegram bericht (HTML parse mode).
    """
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message_html,
        "parse_mode": TELEGRAM_PARSE_MODE,
        "disable_web_page_preview": True,
    }

    r = requests.post(url, data=payload, timeout=20)
    r.raise_for_status()

def fetch_html_with_cookiewall(url: str) -> Tuple[str, requests.Session]:
    """
    Haalt HTML op en accepteert cookiewall als die verschijnt.
    Geeft (html, session) terug zodat we dezelfde cookies kunnen hergebruiken.
    """
    session = requests.Session()

    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) "
                      "Chrome/122.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "nl-NL,nl;q=0.9,en;q=0.8",
    }

    r1 = session.get(url, headers=headers, timeout=25, allow_redirects=True)
    r1.raise_for_status()

    # Cookiewall?
    if "/cookiewall" in r1.url or "cookiewall" in r1.text.lower():
        soup = BeautifulSoup(r1.text, "html.parser")
        form = soup.find("form", attrs={"action": "/cookiewall/Save"})
        if not form:
            return r1.text, session  # fallback

        payload = {}
        for inp in form.select('input[type="hidden"]'):
            name = inp.get("name")
            value = inp.get("value", "")
            if name:
                payload[name] = value

        payload.setdefault("SettingsOpen", "false")

        save_url = "https://badmintonnederland.toernooi.nl/cookiewall/Save"
        r2 = session.post(save_url, data=payload, headers=headers, timeout=25, allow_redirects=True)
        r2.raise_for_status()

        r3 = session.get(url, headers=headers, timeout=25, allow_redirects=True)
        r3.raise_for_status()
        return r3.text, session

    return r1.text, session

def fetch_html(url: str, session: requests.Session) -> str:
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) "
                      "Chrome/122.0.0.0 Safari/537.36",
        "Accept-Language": "nl-NL,nl;q=0.9,en;q=0.8",
    }
    r = session.get(url, headers=headers, timeout=25, allow_redirects=True)
    r.raise_for_status()
    return r.text

def parse_score(score: str) -> Optional[Tuple[int, int]]:
    m = re.match(r"^\s*(\d+)\s*-\s*(\d+)\s*$", score)
    if not m:
        return None
    return int(m.group(1)), int(m.group(2))

def result_emoji(home: str, away: str, score: str) -> str:
    parsed = parse_score(score)
    if not parsed:
        return ""
    home_pts, away_pts = parsed

    if home_pts == away_pts:
        return "➖"

    # team-perspectief
    if home.strip() == TEAM_NAME:
        return "✅" if home_pts > away_pts else "❌"
    if away.strip() == TEAM_NAME:
        return "✅" if away_pts > home_pts else "❌"
    return ""  # zou niet moeten gebeuren

def extract_overview(html: str) -> Tuple[List[Dict], List[Dict]]:
    """
    Retourneert (played, upcoming) als lists van dicts:
    played item: {dt, home, away, score, match_id, line}
    upcoming item: {dt, home, away, match_id, line}
    """
    soup = BeautifulSoup(html, "html.parser")
    table = soup.select_one("table.ruler.matches")
    if not table:
        return [], []

    played: List[Dict] = []
    upcoming: List[Dict] = []

    for tr in table.select("tbody tr"):
        dt_td = tr.select_one("td.plannedtime")
        dt = " ".join(dt_td.get_text(" ", strip=True).split()) if dt_td else ""

        tds = tr.find_all("td", recursive=False)
        if len(tds) < 10:
            continue

        team_links = tr.select("a.teamname")
        if len(team_links) < 2:
            continue
        home = team_links[0].get_text(strip=True)
        away = team_links[1].get_text(strip=True)

        match_id = None
        for a in team_links[:2]:
            href = a.get("href", "")
            mm = re.search(r"(?:\?|&)\s*match=(\d+)", href)
            if mm:
                match_id = mm.group(1)
                break

        score_span = tr.select_one("span.score")
        score = score_span.get_text(" ", strip=True) if score_span else ""
        score = " ".join(score.split())

        if score:
            emo = result_emoji(home, away, score)
            line = f"{dt} — {home} - {away} : {score} {emo}".rstrip()
            played.append({
                "dt": dt, "home": home, "away": away,
                "score": score, "match_id": match_id, "line": line
            })
        else:
            line = f"{dt} — {home} - {away}".rstrip()
            upcoming.append({
                "dt": dt, "home": home, "away": away,
                "match_id": match_id, "line": line
            })

    return played, upcoming

def fetch_match_detail(match_id: Optional[str], session: requests.Session) -> List[str]:
    """
    Haalt het 'Wedstrijdoverzicht' op (MD/VD/ME1/... met namen + setstanden).
    Geeft een lijst strings terug.
    """
    if not match_id:
        return []

    url = f"https://badmintonnederland.toernooi.nl/sport/teammatch.aspx?id={COMP_ID}&match={match_id}"
    html = fetch_html(url, session)

    soup = BeautifulSoup(html, "html.parser")

    target = None
    for t in soup.select("table.ruler.matches"):
        cap = t.find("caption")
        if cap and "Wedstrijdoverzicht" in cap.get_text(strip=True):
            target = t
            break
    if not target:
        return []

    lines: List[str] = []
    for tr in target.select("tbody tr"):
        cells = tr.find_all("td", recursive=False)
        if len(cells) < 5:
            continue

        onderdeel = cells[0].get_text(" ", strip=True)
        if not onderdeel:
            continue

        team_a_names = [a.get_text(" ", strip=True) for a in cells[1].select("a") if a.get_text(strip=True)]
        team_b_names = [a.get_text(" ", strip=True) for a in cells[3].select("a") if a.get_text(strip=True)]

        team_a = " / ".join(team_a_names) if team_a_names else "-"
        team_b = " / ".join(team_b_names) if team_b_names else "-"

        set_spans = [s.get_text(strip=True) for s in cells[4].select("span.score span") if s.get_text(strip=True)]
        sets = " ".join(set_spans) if set_spans else cells[4].get_text(" ", strip=True)
        sets = " ".join(sets.split()) if sets else "-"

        lines.append(f"{onderdeel} — {team_a} vs {team_b} : {sets}")

    return lines

# ---------- Standen ----------
def extract_standings(html: str) -> List[Dict[str, str]]:
    """
    Parseert de standen tabel op teamstandings.aspx
    """
    soup = BeautifulSoup(html, "html.parser")
    table = soup.select_one("table.ruler")
    if not table:
        return []

    rows: List[Dict[str, str]] = []
    for tr in table.select("tbody tr"):
        tds = tr.find_all("td", recursive=False)
        if len(tds) < 6:
            continue

        rank = tds[0].get_text(" ", strip=True)
        team_link = tds[1].select_one("a")
        team = team_link.get_text(" ", strip=True) if team_link else tds[1].get_text(" ", strip=True)

        def td(i: int) -> str:
            return tds[i].get_text(" ", strip=True) if i < len(tds) else ""

        rows.append({
            "rank": rank,
            "team": team,
            "punten": td(2),
            "gespeeld": td(3),
            "gewonnen": td(4),
            "gelijk": td(5),
            "verloren": td(6) if len(tds) > 6 else "",
            "wedstrijden": td(7),
            "games": td(8),
        })

    return rows

# ---------- HTML helpers ----------
def html_escape(s: str) -> str:
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

def render_list(items: List[str]) -> str:
    if not items:
        return '<div style="font-size:13px;color:#6b7280;">(geen items)</div>'

    rows = []
    for line in items:
        rows.append(
            f'<tr>'
            f'  <td style="padding:10px 12px;border-bottom:1px solid #eef2f7;font-size:13px;color:#111827;line-height:18px;">'
            f'    {html_escape(line)}'
            f'  </td>'
            f'</tr>'
        )
    return (
        '<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" '
        'style="border:1px solid #eef2f7;border-radius:12px;overflow:hidden;background:#ffffff;">'
        + "".join(rows) +
        '</table>'
    )

def render_details(detail_lines: List[str]) -> str:
    if not detail_lines:
        return '<div style="font-size:13px;color:#6b7280;">(nog geen detailuitslag beschikbaar)</div>'

    rows = []
    for l in detail_lines:
        rows.append(
            f'<tr>'
            f'  <td style="padding:10px 12px;border-bottom:1px solid #eef2f7;font-size:13px;color:#111827;line-height:18px;">'
            f'    {html_escape(l)}'
            f'  </td>'
            f'</tr>'
        )
    return (
        '<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" '
        'style="border:1px solid #eef2f7;border-radius:12px;overflow:hidden;background:#ffffff;">'
        + "".join(rows) +
        '</table>'
    )

def render_standings_table(standings: List[Dict[str, str]]) -> str:
    if not standings:
        return '<div style="font-size:13px;color:#6b7280;">(stand niet gevonden)</div>'

    header = (
        "<tr>"
        "<th align='left' style='padding:10px 10px;border-bottom:1px solid #e5e7eb;font-size:12px;color:#6b7280;text-transform:uppercase;letter-spacing:.06em;'>#</th>"
        "<th align='left' style='padding:10px 10px;border-bottom:1px solid #e5e7eb;font-size:12px;color:#6b7280;text-transform:uppercase;letter-spacing:.06em;'>Team</th>"
        "<th align='right' style='padding:10px 10px;border-bottom:1px solid #e5e7eb;font-size:12px;color:#6b7280;text-transform:uppercase;letter-spacing:.06em;'>Ptn</th>"
        "<th align='right' style='padding:10px 10px;border-bottom:1px solid #e5e7eb;font-size:12px;color:#6b7280;text-transform:uppercase;letter-spacing:.06em;'>Gesp</th>"
        "<th align='right' style='padding:10px 10px;border-bottom:1px solid #e5e7eb;font-size:12px;color:#6b7280;text-transform:uppercase;letter-spacing:.06em;'>W</th>"
        "<th align='right' style='padding:10px 10px;border-bottom:1px solid #e5e7eb;font-size:12px;color:#6b7280;text-transform:uppercase;letter-spacing:.06em;'>G</th>"
        "<th align='right' style='padding:10px 10px;border-bottom:1px solid #e5e7eb;font-size:12px;color:#6b7280;text-transform:uppercase;letter-spacing:.06em;'>V</th>"
        "</tr>"
    )

    rows_html = []
    for r in standings:
        is_us = (r.get("team", "").strip() == TEAM_NAME)
        bg = "background:#ecfdf5;" if is_us else "background:#ffffff;"
        fw = "font-weight:700;" if is_us else "font-weight:400;"

        rows_html.append(
            f"<tr style='{bg}'>"
            f"<td style='padding:10px 10px;border-bottom:1px solid #eef2f7;font-size:13px;color:#111827;{fw}width:40px;'>{html_escape(r.get('rank',''))}</td>"
            f"<td style='padding:10px 10px;border-bottom:1px solid #eef2f7;font-size:13px;color:#111827;{fw}'>{html_escape(r.get('team',''))}</td>"
            f"<td style='padding:10px 10px;border-bottom:1px solid #eef2f7;font-size:13px;color:#111827;text-align:right;{fw}'>{html_escape(r.get('punten',''))}</td>"
            f"<td style='padding:10px 10px;border-bottom:1px solid #eef2f7;font-size:13px;color:#111827;text-align:right;{fw}'>{html_escape(r.get('gespeeld',''))}</td>"
            f"<td style='padding:10px 10px;border-bottom:1px solid #eef2f7;font-size:13px;color:#111827;text-align:right;{fw}'>{html_escape(r.get('gewonnen',''))}</td>"
            f"<td style='padding:10px 10px;border-bottom:1px solid #eef2f7;font-size:13px;color:#111827;text-align:right;{fw}'>{html_escape(r.get('gelijk',''))}</td>"
            f"<td style='padding:10px 10px;border-bottom:1px solid #eef2f7;font-size:13px;color:#111827;text-align:right;{fw}'>{html_escape(r.get('verloren',''))}</td>"
            f"</tr>"
        )

    return (
        "<table role='presentation' width='100%' cellspacing='0' cellpadding='0' border='0' "
        "style='border:1px solid #eef2f7;border-radius:12px;overflow:hidden;background:#ffffff;'>"
        f"{header}{''.join(rows_html)}"
        "</table>"
    )

def build_standings_text(standings: List[Dict[str, str]]) -> str:
    if not standings:
        return "(stand niet gevonden)"
    lines = []
    for r in standings:
        prefix = "👉 " if r.get("team", "").strip() == TEAM_NAME else "   "
        lines.append(
            f"{prefix}{r.get('rank','')}. {r.get('team','')} — {r.get('punten','')} ptn "
            f"(W{r.get('gewonnen','')}/G{r.get('gelijk','')}/V{r.get('verloren','')})"
        )
    return "\n".join(lines)

# ---------- Builders ----------
def build_html(
    played: List[Dict],
    upcoming: List[Dict],
    detail_lines: List[str],
    last_match_id: Optional[str],
    standings: List[Dict[str, str]],
) -> str:
    last_line = played[-1]["line"] if played else "(nog geen uitslagen gevonden)"

    with open(TEMPLATE_FILE, "r", encoding="utf-8") as f:
        tpl = f.read()

    detail_html = render_details(detail_lines)
    played_html = render_list([m["line"] for m in played])
    upcoming_html = render_list([m["line"] for m in upcoming])
    standings_html = render_standings_table(standings)

    return tpl.format(
        team_name=html_escape(TEAM_NAME),
        overview_url=html_escape(URL_OVERVIEW),
        standings_url=html_escape(URL_STANDINGS),
        last_line=html_escape(last_line),
        last_match_id=html_escape(last_match_id or "-"),
        detail_html=detail_html,
        played_html=played_html,
        upcoming_html=upcoming_html,
        standings_html=standings_html,
        generated_at=datetime.now().strftime("%d-%m-%Y %H:%M:%S"),
    )

def build_body(
    played: List[Dict],
    upcoming: List[Dict],
    detail_lines: List[str],
    standings: List[Dict[str, str]],
) -> str:
    last_line = played[-1]["line"] if played else "(nog geen uitslagen gevonden)"
    detail_block = "\n".join(detail_lines) if detail_lines else "(nog geen detailuitslag beschikbaar)"

    played_block = "\n".join([m["line"] for m in played]) if played else "(nog geen gespeelde wedstrijden)"
    upcoming_block = "\n".join([m["line"] for m in upcoming]) if upcoming else "(geen komende wedstrijden gevonden)"
    standings_block = build_standings_text(standings)

    body = (
        "Er is een update in het teamoverzicht gedetecteerd.\n\n"
        f"Pagina:\n{URL_OVERVIEW}\n\n"
        f"Laatste uitslag:\n{last_line}\n\n"
        f"Detailuitslag laatste wedstrijd:\n{detail_block}\n\n"
        "Alle gespeelde wedstrijden:\n"
        f"{played_block}\n\n"
        "Komende wedstrijden:\n"
        f"{upcoming_block}\n\n"
        "Stand:\n"
        f"{standings_block}\n\n"
        f"Stand pagina:\n{URL_STANDINGS}\n"
    )
    return body

def esc_tg(s: str) -> str:
    # Telegram HTML escape
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

def build_telegram_message(
    played: List[Dict],
    upcoming: List[Dict],
    detail_lines: List[str],
    standings: List[Dict[str, str]],
) -> str:
    last_line = played[-1]["line"] if played else "(nog geen uitslagen gevonden)"

    # Neem max 8 detailregels (Telegram wordt snel lang)
    detail_short = detail_lines[:8]
    detail_block = "\n".join(f"• {esc_tg(x)}" for x in detail_short) if detail_short else "• (nog geen detailuitslag)"

    # Volgende wedstrijd (eerste upcoming)
    next_match = upcoming[0]["line"] if upcoming else "(geen komende wedstrijd gevonden)"

    # Stand: pak alleen jouw teamregel (of top 6 als fallback)
    us = next((r for r in standings if (r.get("team","").strip() == TEAM_NAME)), None)
    if us:
        stand_line = f"{us.get('rank','?')}. {esc_tg(us.get('team',''))} — {esc_tg(us.get('punten',''))} ptn (W{esc_tg(us.get('gewonnen',''))}/G{esc_tg(us.get('gelijk',''))}/V{esc_tg(us.get('verloren',''))})"
        stand_block = f"• <b>Plaats op de ranglijst:</b> {stand_line}"
    else:
        top = standings[:6]
        stand_block = "\n".join(
            f"• {esc_tg(r.get('rank',''))}. {esc_tg(r.get('team',''))} — {esc_tg(r.get('punten',''))} ptn"
            for r in top
        ) if top else "• (stand niet gevonden)"

    msg = (
        f"🏸 <b>{esc_tg(TEAM_NAME)} – update</b>\n"
        f"<i>{datetime.now().strftime('%d-%m-%Y %H:%M:%S')}</i>\n\n"
        f"📌 <b>Laatste (tussen)stand</b>\n"
        f"• {esc_tg(last_line)}\n\n"
        f"🧾 <b>Detail (laatst ingevuld)</b>\n"
        f"{detail_block}\n\n"
        f"🗓️ <b>Volgende wedstrijd</b>\n"
        f"• {esc_tg(next_match)}\n\n"
        f"📊 <b>Stand</b>\n"
        f"{stand_block}\n\n"
        f"🔗 <a href=\"{esc_tg(URL_OVERVIEW)}\">Wedstrijden</a>  |  "
        f"<a href=\"{esc_tg(URL_STANDINGS)}\">Stand</a>"
    )
    return msg

def main():
    html, session = fetch_html_with_cookiewall(URL_OVERVIEW)
    played, upcoming = extract_overview(html)

    # Detail van laatste gespeelde wedstrijd
    last_match_id = played[-1]["match_id"] if played else None
    detail_lines = fetch_match_detail(last_match_id, session) if last_match_id else []

    # Standen ophalen (NIET in hash meenemen, alleen tonen in mail)
    standings_page = fetch_html(URL_STANDINGS, session)
    standings = extract_standings(standings_page)

    # Hash alleen op basis van wedstrijden/planning (zoals je wil)
    combined_for_hash = "\n".join([m["line"] for m in played] + ["---"] + [m["line"] for m in upcoming])
    current_hash = hashlib.sha256(combined_for_hash.encode("utf-8")).hexdigest()

    state = load_state()
    previous_hash = state.get("hash")
    first_run = previous_hash is None

    if first_run or previous_hash != current_hash:
        subject = f"🏸 Update {TEAM_NAME}"
        body_text = build_body(played, upcoming, detail_lines, standings)
        body_html = build_html(played, upcoming, detail_lines, last_match_id, standings)
        send_email(subject, body_text, body_html)
        tg_msg = build_telegram_message(played, upcoming, detail_lines, standings)
        send_telegram(tg_msg)

        state["hash"] = current_hash
        state["played"] = played
        state["upcoming"] = upcoming
        state["last_match_id"] = last_match_id
        # optioneel: opslaan voor debugging (geen invloed op mail-trigger)
        state["standings"] = standings
        save_state(state)

        print("Mail verstuurd.")
    else:
        print("Geen wijziging, geen mail verstuurd.")

if __name__ == "__main__":
    main()
