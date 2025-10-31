#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
KJPP Job Monitor — Telegram + results export
Scans career pages listed in job_urls.txt, classifies matches, and
sends a Telegram message with KJPP-physician roles first, then related roles.

Creates:
  - state.json              (seen items across runs)
  - last_results.txt        (all matches in this run, ordered KJPP → RELATED)
  - last_new_results.txt    (only new matches in this run, ordered KJPP → RELATED)

Env (Secrets):
  TELEGRAM_BOT_TOKEN
  TELEGRAM_CHAT_ID

Files (repo root):
  job_urls.txt       # one URL per line (career/Jobs pages)
"""

import os
import re
import json
import time
import hashlib
from pathlib import Path
from typing import List, Dict, Tuple
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

# --------------------- CONFIG ---------------------

STATE_FILE = "state.json"
URLS_FILE = "job_urls.txt"
LAST_RESULTS_FILE = "last_results.txt"
LAST_NEW_RESULTS_FILE = "last_new_results.txt"

TIMEOUT = 30
SLEEP_BETWEEN = 2.0  # polite crawling pause (seconds)
USER_AGENT = "KJPP-JobMonitor/2.1 (+telegram-only)"

# If True → send RELATED roles too (after KJPP). If False → only KJPP.
INCLUDE_RELATED = True

# Telegram (required)
TG_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TG_CHAT = os.getenv("TELEGRAM_CHAT_ID", "")

HEADERS = {"User-Agent": USER_AGENT}

# ------------------ PATTERN SETS ------------------

# Broad KJPP-related signals (kept for discovery)
KEYWORDS = [
    r"kinder-?\s*und-?\s*jugendpsychi",
    r"kinder-?\s*und-?\s*jugendpsychother",
    r"\bkjpp\b", r"\bkjp\b",
    r"jugendpsychiatr", r"kinderpsychiatr",
]
KEYWORDS_RE = re.compile("(" + "|".join(KEYWORDS) + ")", re.I | re.U)

# Stricter patterns → true *physician* KJPP roles
STRICT_KJPP_PATTERNS = [
    r"\bfacharzt\b.*kinder.*jugendpsychiatr",
    r"\boberarzt\b.*kinder.*jugendpsychiatr",
    r"\bassistenzarzt\b.*kinder.*jugendpsychiatr",
    r"\bweiterbildungsassistent\b.*kinder.*jugendpsychiatr",
    r"\bärztin?\b.*kinder.*jugendpsychiatr",
    r"\barzt\b.*kinder.*jugendpsychiatr",
    r"\b(w|weiterbildung).*(kinder.*jugendpsychiatr)",
    r"\bkinder-?\s*und-?\s*jugendpsychiatr.*(arzt|ärztin|facharzt|oberarzt|assistenzarzt)",
]
STRICT_KJPP_RE = re.compile("(" + "|".join(STRICT_KJPP_PATTERNS) + ")", re.I | re.U)

# Related roles in the KJPP setting (optional)
RELATED_PATTERNS = [
    r"psycholog.*kinder", r"psychotherapeut.*jugend", r"therapeut.*jugend",
    r"pflege.*jugendpsychiatr", r"erzieher.*jugendpsychiatr",
    r"pädagog.*jugendpsychiatr", r"sozialarbeit.*jugendpsychiatr",
]
RELATED_RE = re.compile("(" + "|".join(RELATED_PATTERNS) + ")", re.I | re.U)

# Links we generally ignore
BLOCK_PATH_WORDS = [
    "impressum", "datenschutz", "privacy", "agb", "kontakt",
    "login", "sitemap", "newsletter"
]

# Typical job subpaths
JOB_HINTS = [
    "/stellen", "/jobs", "/karriere", "/bewerb",
    "/stellenangebot", "/ausschreibung", "vacanc", "job",
    "position", "medizin", "arzt", "psycholog"
]

# ------------------ UTILITIES ---------------------

def load_state() -> Dict[str, float]:
    if Path(STATE_FILE).exists():
        try:
            return json.loads(Path(STATE_FILE).read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}

def save_state(state: Dict[str, float]) -> None:
    Path(STATE_FILE).write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

def http_get(url: str) -> str:
    r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    return r.text

def normalize(base: str, href: str) -> str:
    return urljoin(base, (href or "").strip())

def looks_like_job_link(href: str, text: str) -> bool:
    if not href:
        return False
    low = href.lower()
    if any(w in low for w in BLOCK_PATH_WORDS):
        return False
    if any(h in low for h in JOB_HINTS):
        return True
    return bool(KEYWORDS_RE.search(text or ""))

def extract_candidates(html: str, base_url: str) -> List[Dict]:
    soup = BeautifulSoup(html, "lxml")
    items = []
    # 1) links
    for a in soup.find_all("a"):
        href = normalize(base_url, a.get("href"))
        text = (a.get_text(" ", strip=True) or "").strip()
        if not href.startswith(("http://", "https://")):
            continue
        if looks_like_job_link(href, text) or KEYWORDS_RE.search(text):
            items.append({"href": href, "title": text or href})
    # 2) fallback: whole page mentions KJPP keywords
    if KEYWORDS_RE.search(soup.get_text(" ", strip=True)):
        items.append({"href": base_url, "title": "Hinweis: Keywords auf Seite gefunden"})
    # dedupe
    seen, uniq = set(), []
    for it in items:
        k = it["href"].strip()
        if k not in seen:
            seen.add(k)
            uniq.append(it)
    return uniq

def classify_hit(title: str, url: str) -> str:
    """Return 'KJPP' (physician), 'RELATED' (other KJPP-area roles), or 'OTHER'."""
    text = f"{title or ''} {url}".lower()
    if STRICT_KJPP_RE.search(text):
        return "KJPP"
    if RELATED_RE.search(text) or KEYWORDS_RE.search(text):
        return "RELATED"
    return "OTHER"

def make_id(url: str, title: str) -> str:
    return hashlib.sha256((url + "|" + (title or "")).encode("utf-8")).hexdigest()[:24]

def tgsend(text: str):
    if not TG_TOKEN or not TG_CHAT:
        print("[WARN] Telegram not configured; skipping send.")
        return
    chunks = [text[i:i+3500] for i in range(0, len(text), 3500)] or [text]
    for c in chunks:
        try:
            requests.post(
                f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
                json={"chat_id": TG_CHAT, "text": c, "disable_web_page_preview": True},
                timeout=30,
            )
        except Exception as e:
            print("[WARN] Telegram send error:", e)

def write_txt(path: str, header: str, lines: List[str]) -> None:
    """Write a simple readable text export."""
    body = header.rstrip() + "\n\n"
    if lines:
        body += "\n".join(lines) + "\n"
    else:
        body += "(keine Treffer)\n"
    Path(path).write_text(body, encoding="utf-8")

# ------------------ MAIN LOGIC ---------------------

def run_once() -> int:
    # basic checks
    if not TG_TOKEN or not TG_CHAT:
        raise SystemExit("Bitte TELEGRAM_BOT_TOKEN und TELEGRAM_CHAT_ID als Umgebungsvariablen setzen.")
    if not Path(URLS_FILE).exists():
        raise SystemExit(f"{URLS_FILE} fehlt.")

    urls = [
        u.strip()
        for u in Path(URLS_FILE).read_text(encoding="utf-8").splitlines()
        if u.strip() and not u.strip().startswith("#")
    ]

    state = load_state()
    new_items: List[Tuple[int, str]] = []   # (priority, line)
    all_items: List[Tuple[int, str]] = []   # (priority, line)

    for url in urls:
        try:
            html = http_get(url)
            candidates = extract_candidates(html, url)

            # classify and filter
            filtered = []
            for c in candidates:
                cls = classify_hit(c.get("title", ""), c["href"])
                if cls == "KJPP":
                    c["cls"] = "KJPP"
                    filtered.append(c)
                elif INCLUDE_RELATED and cls == "RELATED":
                    c["cls"] = "RELATED"
                    filtered.append(c)
                # OTHER → ignored

            # Build "all" list (not deduped by state)
            for c in filtered:
                label = c["cls"]  # 'KJPP' or 'RELATED'
                prio = 0 if label == "KJPP" else 1
                all_items.append((prio, f"• [{label}] {c.get('title','(ohne Titel)')}\n  {c['href']}"))

            # New-only decisions (state)
            for c in filtered:
                uid = make_id(c["href"], c.get("title", ""))
                if uid not in state:
                    state[uid] = time.time()
                    label = c["cls"]
                    line = f"• [{label}] {c.get('title','(ohne Titel)')}\n  {c['href']}"
                    prio = 0 if label == "KJPP" else 1
                    new_items.append((prio, line))
        except Exception as e:
            warn = f"[WARN] {url}: {e}"
            all_items.append((2, warn))
            new_items.append((2, warn))
        time.sleep(SLEEP_BETWEEN)

    save_state(state)

    # Sort & export text files
    all_items.sort(key=lambda x: x[0])
    new_items.sort(key=lambda x: x[0])

    write_txt(LAST_RESULTS_FILE, "Alle Treffer dieses Laufs (KJPP zuerst):", [ln for _, ln in all_items])
    write_txt(LAST_NEW_RESULTS_FILE, "Neue Treffer dieses Laufs (KJPP zuerst):", [ln for _, ln in new_items])

    # Telegram message for new items only
    if new_items:
        body = "🆕 Neue Stellen (KJPP zuerst):\n\n" + "\n".join(line for _, line in new_items)
        print(body)
        tgsend(body)
        return len(new_items)

    print("Keine neuen Treffer.")
    return 0

if __name__ == "__main__":
    hits = run_once()
    print(f"Done. Neue Treffer: {hits}")
