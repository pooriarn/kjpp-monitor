#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
KJPP Job Monitor — Specialized parser for KVB Baden-Württemberg
"""

import os
import re
import json
import time
import hashlib
from pathlib import Path
from typing import List, Dict, Tuple
from urllib.parse import urljoin, urlencode
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
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

INCLUDE_RELATED = True

TG_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TG_CHAT = os.getenv("TELEGRAM_CHAT_ID", "")

HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "de-DE,de;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "DNT": "1",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
}

# ------------------ PATTERN SETS ------------------

STRICT_KJPP_PATTERNS = [
    r"\b(facharzt|oberarzt|assistenzarzt|arzt|ärztin)\b.*\b(kinder-?und-?jugendpsychiatrie|kinder-?jugend-?psychiatrie)\b",
    r"\b(kinder-?und-?jugendpsychiatrie|kinder-?jugend-?psychiatrie)\b.*\b(facharzt|oberarzt|assistenzarzt|arzt|ärztin)\b",
    r"\b(kjp|kjpp)\b.*\b(arzt|ärztin|facharzt|oberarzt|assistenzarzt)\b",
    r"\b(arzt|ärztin|facharzt|oberarzt|assistenzarzt)\b.*\b(kjp|kjpp)\b",
]

RELATED_PATTERNS = [
    r"\b(psychologe|psychologin|psychotherapeut|psychotherapeutin)\b.*\b(kinder|jugend)\b",
    r"\b(kinder|jugend)\b.*\b(psychologe|psychologin|psychotherapeut|psychotherapeutin)\b",
]

EXCLUSION_PATTERNS = [
    r"niederlassung", r"praxisbörse", r"beratung", r"hilfestellung", 
    r"famulatur", r"pj-", r"allgemeinmedizin", r"hausarzt",
]

STRICT_KJPP_RE = re.compile("(" + "|".join(STRICT_KJPP_PATTERNS) + ")", re.I | re.U)
RELATED_RE = re.compile("(" + "|".join(RELATED_PATTERNS) + ")", re.I | re.U)
EXCLUSION_RE = re.compile("(" + "|".join(EXCLUSION_PATTERNS) + ")", re.I | re.U)

# ------------------ SPECIALIZED PARSERS ---------------------

def parse_kvb_bawue(url: str) -> List[Dict]:
    """
    Specialized parser for KVB Baden-Württemberg job portal
    This site has a structured JSON/AJAX format
    """
    print(f"    Using KVB Baden-Württemberg parser...")
    
    try:
        # First, let's see what the page contains
        response = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        response.raise_for_status()
        
        print(f"    Response status: {response.status_code}")
        print(f"    Content type: {response.headers.get('content-type')}")
        print(f"    Content length: {len(response.text)}")
        
        # Check if it's JSON (AJAX response)
        if 'application/json' in response.headers.get('content-type', ''):
            return parse_kvb_bawue_json(response.json(), url)
        else:
            return parse_kvb_bawue_html(response.text, url)
            
    except Exception as e:
        print(f"    KVB parser error: {e}")
        return []

def parse_kvb_bawue_json(data: dict, base_url: str) -> List[Dict]:
    """Parse JSON response from KVB Baden-Württemberg"""
    items = []
    
    print(f"    Parsing JSON data structure...")
    
    try:
        # Try different possible JSON structures
        if isinstance(data, list):
            # Direct list of jobs
            for job in data:
                if isinstance(job, dict):
                    title = job.get('titel') or job.get('title') or job.get('bezeichnung') or ''
                    link = job.get('link') or job.get('url') or base_url
                    
                    if title:
                        items.append({
                            "href": urljoin(base_url, link),
                            "title": title,
                            "source": "json_list"
                        })
                        
        elif isinstance(data, dict):
            # Look for jobs in nested structures
            if 'items' in data:
                return parse_kvb_bawue_json(data['items'], base_url)
            elif 'results' in data:
                return parse_kvb_bawue_json(data['results'], base_url)
            elif 'data' in data:
                return parse_kvb_bawue_json(data['data'], base_url)
                
            # Check if this is a single job
            title = data.get('titel') or data.get('title') or data.get('bezeichnung') or ''
            if title:
                items.append({
                    "href": base_url,
                    "title": title,
                    "source": "json_single"
                })
                
    except Exception as e:
        print(f"    JSON parsing error: {e}")
        
    print(f"    Found {len(items)} items in JSON")
    return items

def parse_kvb_bawue_html(html: str, base_url: str) -> List[Dict]:
    """Parse HTML response from KVB Baden-Württemberg"""
    soup = BeautifulSoup(html, "lxml")
    items = []
    
    print(f"    Parsing HTML structure...")
    
    # Look for common job listing patterns in German portals
    job_selectors = [
        '.job-item', '.stellenangebot', '.angebot-item',
        '.result-item', '.list-item', '.teaser',
        'article.job', 'div.job', 'li.job',
        '[class*="boerse"]', '[class*="angebot"]',
        '.tx-boersen-ext1',  # Common class from the URL
    ]
    
    for selector in job_selectors:
        elements = soup.select(selector)
        for element in elements:
            try:
                # Extract title
                title_elem = element.select_one('h1, h2, h3, h4, .title, .headline, .titel')
                title = title_elem.get_text(" ", strip=True) if title_elem else element.get_text(" ", strip=True)
                
                # Extract link
                link_elem = element.select_one('a[href]')
                href = link_elem.get('href') if link_elem else base_url
                full_url = urljoin(base_url, href)
                
                if title and len(title) > 10:
                    items.append({
                        "href": full_url,
                        "title": title[:300],
                        "source": f"html_{selector}"
                    })
                    
            except Exception as e:
                continue
    
    # Fallback: look for any structured data
    script_tags = soup.find_all('script', type='application/ld+json')
    for script in script_tags:
        try:
            data = json.loads(script.string)
            if isinstance(data, dict) and data.get('@type') == 'JobPosting':
                title = data.get('title', '')
                if title:
                    items.append({
                        "href": data.get('url', base_url),
                        "title": title,
                        "source": "structured_data"
                    })
        except:
            pass
    
    print(f"    Found {len(items)} items in HTML")
    return items

def parse_kvboerse_general(url: str) -> List[Dict]:
    """General parser for KVBOERSE and similar sites"""
    print(f"    Using general KV parser...")
    
    try:
        response = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "lxml")
        
        items = []
        
        # Look for job containers
        job_containers = soup.find_all(class_=re.compile(r'job|stelle|angebot', re.I))
        
        for container in job_containers:
            text = container.get_text(" ", strip=True)
            if len(text) < 30:
                continue
                
            link = container.find('a', href=True)
            href = link.get('href') if link else url
            full_url = urljoin(url, href)
            
            items.append({
                "href": full_url,
                "title": text[:300],
                "source": "general_container"
            })
        
        return items
        
    except Exception as e:
        print(f"    General parser error: {e}")
        return []

# ------------------ IMPROVED CLASSIFICATION ---------------------

def is_false_positive(title: str, url: str) -> bool:
    """Check if this is a false positive"""
    text = f"{title} {url}".lower()
    
    if EXCLUSION_RE.search(text):
        return True
    
    if len(title) < 30:
        return True
        
    return False

def classify_hit(title: str, url: str) -> str:
    """Classify job postings"""
    if is_false_positive(title, url):
        return "EXCLUDE"
    
    text = f"{title} {url}".lower()
    
    if STRICT_KJPP_RE.search(text):
        return "KJPP"
    
    if RELATED_RE.search(text):
        return "RELATED"
    
    return "OTHER"

# ------------------ CORE FUNCTIONS ---------------------

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

def make_id(url: str, title: str) -> str:
    return hashlib.sha256((url + "|" + (title or "")).encode("utf-8")).hexdigest()[:24]

def tgsend(text: str):
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
        
        # Choose the right parser
        if 'kvbawue' in url:
            candidates = parse_kvb_bawue(url)
        elif 'kvboerse' in url:
            candidates = parse_kvboerse_general(url)
        else:
            candidates = parse_kvboerse_general(url)
        
        # Classify and filter
        filtered = []
        for c in candidates:
            cls = classify_hit(c.get("title", ""), c["href"])
            
            if cls == "EXCLUDE":
                print(f"    ❌ EXCLUDED: {c.get('title', 'No title')}")
                continue
                
            if cls in ["KJPP", "RELATED"]:
                c["cls"] = cls
                filtered.append(c)
                print(f"    ✅ {cls}: {c.get('title', 'No title')}")

        # Build lists
        for c in filtered:
            label = c["cls"]
            prio = 0 if label == "KJPP" else 1
            line = f"• [{label}] {c.get('title','(ohne Titel)')}\n  {c['href']}"
            all_items.append((prio, line))

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
