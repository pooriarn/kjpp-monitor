#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
KJPP Job Monitor — Enhanced version for complex career pages
"""

import os
import re
import json
import time
import hashlib
from pathlib import Path
from typing import List, Dict, Tuple
from urllib.parse import urljoin
from datetime import datetime

import requests
from bs4 import BeautifulSoup

# --------------------- CONFIG ---------------------

STATE_FILE = "state.json"
URLS_FILE = "job_urls.txt"
LAST_RESULTS_FILE = "last_results.txt"
LAST_NEW_RESULTS_FILE = "last_new_results.txt"

TIMEOUT = 30
SLEEP_BETWEEN = 2.0
USER_AGENT = "KJPP-JobMonitor/2.2 (+telegram-only)"

INCLUDE_RELATED = True

TG_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TG_CHAT = os.getenv("TELEGRAM_CHAT_ID", "")

HEADERS = {"User-Agent": USER_AGENT}

# ------------------ PATTERN SETS ------------------

KEYWORDS = [
    r"kinder-?\s*und-?\s*jugendpsychi",
    r"kinder-?\s*und-?\s*jugendpsychother",
    r"\bkjpp\b", r"\bkjp\b",
    r"jugendpsychiatr", r"kinderpsychiatr",
    r"kinder-?jugend-?psych",  # Added more flexible patterns
]
KEYWORDS_RE = re.compile("(" + "|".join(KEYWORDS) + ")", re.I | re.U)

STRICT_KJPP_PATTERNS = [
    r"\bfacharzt\b.*kinder.*jugendpsychiatr",
    r"\boberarzt\b.*kinder.*jugendpsychiatr", 
    r"\bassistenzarzt\b.*kinder.*jugendpsychiatr",
    r"\bweiterbildungsassistent\b.*kinder.*jugendpsychiatr",
    r"\bärztin?\b.*kinder.*jugendpsychiatr",
    r"\barzt\b.*kinder.*jugendpsychiatr",
    r"\b(w|weiterbildung).*(kinder.*jugendpsychiatr)",
    r"\bkinder-?\s*und-?\s*jugendpsychiatr.*(arzt|ärztin|facharzt|oberarzt|assistenzarzt)",
    r"kinder-?jugend-?psychiatr.*arzt",  # More flexible pattern
]
STRICT_KJPP_RE = re.compile("(" + "|".join(STRICT_KJPP_PATTERNS) + ")", re.I | re.U)

RELATED_PATTERNS = [
    r"psycholog.*kinder", r"psychotherapeut.*jugend", r"therapeut.*jugend",
    r"pflege.*jugendpsychiatr", r"erzieher.*jugendpsychiatr",
    r"pädagog.*jugendpsychiatr", r"sozialarbeit.*jugendpsychiatr",
]
RELATED_RE = re.compile("(" + "|".join(RELATED_PATTERNS) + ")", re.I | re.U)

# ------------------ ENHANCED PARSING ---------------------

def load_state() -> Dict[str, float]:
    if Path(STATE_FILE).exists():
        try:
            return json.loads(Path(STATE_FILE).read_text(encoding="utf-8"))
        except Exception as e:
            print(f"[WARN] Could not load state: {e}")
            return {}
    return {}

def save_state(state: Dict[str, float]) -> None:
    try:
        Path(STATE_FILE).write_text(
            json.dumps(state, ensure_ascii=False, indent=2), 
            encoding="utf-8"
        )
        print(f"✓ State saved: {STATE_FILE}")
    except Exception as e:
        print(f"✗ Error saving state: {e}")

def http_get(url: str) -> str:
    r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    return r.text

def extract_jobs_from_content_page(html: str, base_url: str) -> List[Dict]:
    """
    Enhanced extraction for content pages like Wichernstift
    that have job listings embedded in the page content.
    """
    soup = BeautifulSoup(html, "lxml")
    items = []
    
    print(f"    Analyzing page content for job patterns...")
    
    # Method 1: Look for job listings in links
    for a in soup.find_all("a", href=True):
        href = a['href']
        text = a.get_text(" ", strip=True)
        
        # Skip obviously non-job links
        if any(block in href.lower() for block in ["impressum", "datenschutz", "login"]):
            continue
            
        # If link text contains job-related terms, consider it
        if text and any(term in text.lower() for term in ["stellen", "job", "karriere", "bewerbung"]):
            full_url = urljoin(base_url, href)
            items.append({"href": full_url, "title": text, "source": "link"})
    
    # Method 2: Look for job titles in headings and paragraphs
    for tag in soup.find_all(['h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'p', 'li']):
        text = tag.get_text(" ", strip=True)
        if not text or len(text) < 10:
            continue
            
        # Check if this looks like a job title/description
        if (any(term in text.lower() for term in ["stellen", "job", "stelle", "arzt", "psycholog", "therapeut"]) and
            not any(block in text.lower() for block in ["impressum", "datenschutz"])):
            
            # Try to find a link nearby
            link = tag.find('a', href=True)
            if link:
                full_url = urljoin(base_url, link['href'])
                items.append({"href": full_url, "title": text, "source": "heading_with_link"})
            else:
                # Use the page itself as URL if no specific link found
                items.append({"href": base_url, "title": text, "source": "heading"})
    
    # Method 3: Look for specific job pattern matches in any text
    page_text = soup.get_text(" ", strip=True)
    lines = page_text.split('\n')
    
    for line in lines:
        line = line.strip()
        if len(line) < 20:
            continue
            
        # Check for KJPP patterns in any text on the page
        if STRICT_KJPP_RE.search(line) or RELATED_RE.search(line):
            items.append({"href": base_url, "title": line[:200], "source": "text_match"})
    
    # Deduplicate
    seen = set()
    unique_items = []
    for item in items:
        key = item["href"] + "|" + item["title"][:100]
        if key not in seen:
            seen.add(key)
            unique_items.append(item)
    
    print(f"    Found {len(unique_items)} potential job items via enhanced parsing")
    return unique_items

def classify_hit(title: str, url: str) -> str:
    """Classify as 'KJPP' (physician), 'RELATED' (other KJPP-area roles), or 'OTHER'."""
    text = f"{title or ''} {url}".lower()
    if STRICT_KJPP_RE.search(text):
        return "KJPP"
    if RELATED_RE.search(text) or KEYWORDS_RE.search(text):
        return "RELATED"
    return "OTHER"

def make_id(url: str, title: str) -> str:
    """Create unique ID for a job posting."""
    return hashlib.sha256((url + "|" + (title or "")).encode("utf-8")).hexdigest()[:24]

def tgsend(text: str):
    """Send message via Telegram bot."""
    if not TG_TOKEN or not TG_CHAT:
        print("[WARN] Telegram not configured; skipping send.")
        return
    
    chunks = [text[i:i+3500] for i in range(0, len(text), 3500)] or [text]
    for i, chunk in enumerate(chunks):
        try:
            response = requests.post(
                f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
                json={
                    "chat_id": TG_CHAT, 
                    "text": chunk, 
                    "disable_web_page_preview": True
                },
                timeout=30,
            )
            response.raise_for_status()
            print(f"✓ Telegram message {i+1}/{len(chunks)} sent successfully")
        except Exception as e:
            print(f"✗ Telegram send error: {e}")

def write_txt(path: str, header: str, lines: List[str]) -> None:
    """Write a simple readable text export."""
    try:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        body = f"{header} ({timestamp})\n\n"
        if lines:
            body += "\n".join(lines) + "\n"
        else:
            body += "(keine Treffer)\n"
        Path(path).write_text(body, encoding="utf-8")
        print(f"✓ Export saved: {path}")
    except Exception as e:
        print(f"✗ Error writing {path}: {e}")

# ------------------ MAIN LOGIC ---------------------

def run_once() -> int:
    """Run one monitoring cycle. Returns number of new items found."""
    
    if not TG_TOKEN or not TG_CHAT:
        raise SystemExit("ERROR: Bitte TELEGRAM_BOT_TOKEN und TELEGRAM_CHAT_ID als Umgebungsvariablen setzen.")
    
    if not Path(URLS_FILE).exists():
        raise SystemExit(f"ERROR: {URLS_FILE} fehlt.")

    urls = [
        u.strip()
        for u in Path(URLS_FILE).read_text(encoding="utf-8").splitlines()
        if u.strip() and not u.strip().startswith("#")
    ]
    
    print(f"🔍 Monitoring {len(urls)} URLs...")
    
    state = load_state()
    new_items: List[Tuple[int, str]] = []
    all_items: List[Tuple[int, str]] = []

    for i, url in enumerate(urls, 1):
        print(f"  [{i}/{len(urls)}] Checking: {url}")
        try:
            html = http_get(url)
            
            # Use enhanced parsing for content pages
            candidates = extract_jobs_from_content_page(html, url)
            
            # Classify candidates
            filtered = []
            for c in candidates:
                cls = classify_hit(c.get("title", ""), c["href"])
                if cls == "KJPP":
                    c["cls"] = "KJPP"
                    filtered.append(c)
                    print(f"    ✅ KJPP match: {c.get('title', 'No title')}")
                elif INCLUDE_RELATED and cls == "RELATED":
                    c["cls"] = "RELATED"
                    filtered.append(c)
                    print(f"    ✅ RELATED match: {c.get('title', 'No title')}")

            # Build lists
            for c in filtered:
                label = c["cls"]
                prio = 0 if label == "KJPP" else 1
                all_items.append((prio, f"• [{label}] {c.get('title','(ohne Titel)')}\n  {c['href']}"))

            # Check for new items
            for c in filtered:
                uid = make_id(c["href"], c.get("title", ""))
                if uid not in state:
                    state[uid] = time.time()
                    label = c["cls"]
                    line = f"• [{label}] {c.get('title','(ohne Titel)')}\n  {c['href']}"
                    prio = 0 if label == "KJPP" else 1
                    new_items.append((prio, line))
                    print(f"    🆕 NEW: {label} - {c.get('title', c['href'])}")
                    
        except Exception as e:
            warn = f"[WARN] {url}: {e}"
            print(f"    ⚠️  {warn}")
            all_items.append((2, warn))
            new_items.append((2, warn))
        
        time.sleep(SLEEP_BETWEEN)

    save_state(state)

    # Export results
    all_items.sort(key=lambda x: x[0])
    new_items.sort(key=lambda x: x[0])

    write_txt(LAST_RESULTS_FILE, "Alle Treffer dieses Laufs (KJPP zuerst):", [ln for _, ln in all_items])
    write_txt(LAST_NEW_RESULTS_FILE, "Neue Treffer dieses Laufs (KJPP zuerst):", [ln for _, ln in new_items])

    # Send Telegram notification
    if new_items:
        kjpp_items = [line for prio, line in new_items if prio == 0]
        related_items = [line for prio, line in new_items if prio == 1]
        
        body = "🆕 **Neue KJPP-Stellen**\n\n"
        if kjpp_items:
            body += "**👨‍⚕️ KJPP-Arztstellen:**\n" + "\n".join(kjpp_items) + "\n\n"
        if related_items and INCLUDE_RELATED:
            body += "**💼 Verwandte Positionen:**\n" + "\n".join(related_items)
        
        print("📤 Sending Telegram notification...")
        tgsend(body)
        return len(new_items)

    print("✅ Scan completed. No new KJPP positions found.")
    return 0

if __name__ == "__main__":
    try:
        hits = run_once()
        print(f"🎉 Done. Neue Treffer: {hits}")
    except Exception as e:
        print(f"💥 Critical error: {e}")
        exit(1)
