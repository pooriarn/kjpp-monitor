#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
KJPP Job Monitor — Pure Python solution for JavaScript-heavy sites
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

KEYWORDS = [
    r"kinder-?\s*und-?\s*jugendpsychi",
    r"kinder-?\s*und-?\s*jugendpsychother",
    r"\bkjpp\b", r"\bkjp\b",
    r"jugendpsychiatr", r"kinderpsychiatr",
    r"kinder-?jugend-?psych",
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
]
STRICT_KJPP_RE = re.compile("(" + "|".join(STRICT_KJPP_PATTERNS) + ")", re.I | re.U)

# ------------------ SMART PARSING ---------------------

def smart_parse_kvboerse(url: str) -> List[Dict]:
    """
    Smart parsing for KVBOERSE that looks for hidden data and patterns
    """
    print(f"    Using smart KVBOERSE parser...")
    
    try:
        response = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "lxml")
        
        items = []
        
        # Strategy 1: Look for JSON-LD structured data (common in modern sites)
        script_tags = soup.find_all('script', type='application/ld+json')
        for script in script_tags:
            try:
                data = json.loads(script.string)
                if isinstance(data, dict) and data.get('@type') == 'JobPosting':
                    items.append({
                        "href": data.get('url', url),
                        "title": data.get('title', 'Job Posting'),
                        "source": "structured_data"
                    })
                elif isinstance(data, list):
                    for item in data:
                        if isinstance(item, dict) and item.get('@type') == 'JobPosting':
                            items.append({
                                "href": item.get('url', url),
                                "title": item.get('title', 'Job Posting'),
                                "source": "structured_data"
                            })
            except:
                pass
        
        # Strategy 2: Look for hidden meta tags
        meta_tags = soup.find_all('meta')
        for meta in meta_tags:
            name = meta.get('name', '').lower()
            property_attr = meta.get('property', '').lower()
            content = meta.get('content', '')
            
            if any(term in name for term in ['job', 'stelle', 'position']) or \
               any(term in property_attr for term in ['job', 'stelle', 'position']):
                if content and len(content) > 10:
                    items.append({
                        "href": url,
                        "title": content,
                        "source": "meta_tag"
                    })
        
        # Strategy 3: Look for data attributes that might contain job info
        data_attrs = soup.find_all(attrs={"data-job": True})
        for elem in data_attrs:
            job_data = elem.get('data-job', '')
            if job_data and len(job_data) > 10:
                items.append({
                    "href": url,
                    "title": job_data,
                    "source": "data_attr"
                })
        
        # Strategy 4: Deep text analysis - look for job patterns in ALL text
        all_text = soup.get_text()
        lines = [line.strip() for line in all_text.split('\n') if line.strip()]
        
        for line in lines:
            # Skip very short or very long lines
            if len(line) < 20 or len(line) > 500:
                continue
            
            # Look for job title patterns
            if (any(term in line.lower() for term in ['arzt', 'ärztin', 'facharzt', 'assistenzarzt', 'oberarzt']) and
                any(term in line.lower() for term in ['kinder', 'jugend', 'psych', 'stellen', 'job'])):
                
                items.append({
                    "href": url,
                    "title": line,
                    "source": "text_analysis"
                })
        
        # Strategy 5: Look for common CSS classes in job portals
        job_classes = [
            'job', 'stelle', 'position', 'offer', 'listing',
            'teaser', 'card', 'item', 'entry', 'result'
        ]
        
        for class_name in job_classes:
            elements = soup.find_all(class_=re.compile(class_name))
            for elem in elements:
                text = elem.get_text(" ", strip=True)
                if len(text) > 20 and len(text) < 500:
                    # Find link if available
                    link = elem.find('a', href=True)
                    href = link.get('href') if link else url
                    full_url = urljoin(url, href) if href != url else url
                    
                    items.append({
                        "href": full_url,
                        "title": text[:200],  # First 200 chars
                        "source": f"class_{class_name}"
                    })
        
        # Remove duplicates
        seen = set()
        unique_items = []
        for item in items:
            key = item["title"][:100] + item["href"]
            if key not in seen:
                seen.add(key)
                unique_items.append(item)
        
        print(f"    Found {len(unique_items)} potential jobs via smart parsing")
        return unique_items
        
    except Exception as e:
        print(f"    Smart parser error: {e}")
        return []

def parse_simple_site(url: str) -> List[Dict]:
    """
    Parser for simple HTML sites (like Wichernstift)
    """
    print(f"    Using simple site parser...")
    
    try:
        response = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "lxml")
        
        items = []
        
        # Look for job titles in headings and links
        for tag in soup.find_all(['h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'a']):
            text = tag.get_text(" ", strip=True)
            if not text or len(text) < 10:
                continue
                
            # Check if this looks job-related
            if any(term in text.lower() for term in ['stellen', 'job', 'stelle', 'arzt', 'psycholog', 'therapeut']):
                href = tag.get('href') if tag.name == 'a' else None
                full_url = urljoin(url, href) if href else url
                
                items.append({
                    "href": full_url,
                    "title": text,
                    "source": "simple_parser"
                })
        
        print(f"    Found {len(items)} potential jobs on simple site")
        return items
        
    except Exception as e:
        print(f"    Simple parser error: {e}")
        return []

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

def classify_hit(title: str, url: str) -> str:
    """Classify as 'KJPP' (physician), 'RELATED' (other KJPP-area roles), or 'OTHER'."""
    text = f"{title or ''} {url}".lower()
    if STRICT_KJPP_RE.search(text):
        return "KJPP"
    if KEYWORDS_RE.search(text):
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
        
        # Choose the right parser based on URL
        if 'kvboerse' in url:
            candidates = smart_parse_kvboerse(url)
        else:
            candidates = parse_simple_site(url)
        
        # Classify and process candidates
        filtered = []
        for c in candidates:
            cls = classify_hit(c.get("title", ""), c["href"])
            if cls in ["KJPP", "RELATED"]:
                c["cls"] = cls
                filtered.append(c)
                print(f"    ✅ {cls} match: {c.get('title', 'No title')}")

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
