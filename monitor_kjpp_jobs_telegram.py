#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
KJPP Job Monitor — Enhanced for paginated job portals like KVBOERSE
"""

import os
import re
import json
import time
import hashlib
from pathlib import Path
from typing import List, Dict, Tuple, Optional
from urllib.parse import urljoin, urlencode, parse_qs, urlparse
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
USER_AGENT = "KJPP-JobMonitor/4.0 (+pagination)"

INCLUDE_RELATED = True
MAX_PAGES = 10  # Maximum pages to scan per portal

TG_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TG_CHAT = os.getenv("TELEGRAM_CHAT_ID", "")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "de-DE,de;q=0.9,en;q=0.8",
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

# ------------------ PAGINATION HANDLING ---------------------

def get_page_url(base_url: str, page: int) -> str:
    """
    Generate URL for specific page number.
    Handles different pagination styles.
    """
    parsed = urlparse(base_url)
    query_params = parse_qs(parsed.query)
    
    # Common pagination parameter names
    pagination_params = ['page', 'seite', 'p', 'pg', 'offset']
    
    # Check if URL already has pagination
    for param in pagination_params:
        if param in query_params:
            query_params[param] = [str(page)]
            break
    else:
        # No pagination param found, add one
        query_params['page'] = [str(page)]
    
    # Rebuild URL
    new_query = urlencode(query_params, doseq=True)
    return f"{parsed.scheme}://{parsed.netloc}{parsed.path}?{new_query}"

def find_next_page(soup: BeautifulSoup, current_url: str) -> Optional[str]:
    """
    Find next page link from HTML.
    """
    # Common next page selectors
    next_selectors = [
        '.pagination .next a',
        '.pagination a.next',
        '.pager .next a',
        '.pager a.next',
        'a[rel="next"]',
        '.next-page a',
        'li.next a',
        'a:contains("Weitere")',
        'a:contains("nächste")',
        'a:contains("next")',
    ]
    
    for selector in next_selectors:
        next_link = soup.select_one(selector)
        if next_link and next_link.get('href'):
            return urljoin(current_url, next_link['href'])
    
    # Also look for page number links
    page_links = soup.select('.pagination a, .pager a')
    current_page = None
    
    # Try to determine current page and find next
    for link in page_links:
        text = link.get_text(strip=True)
        href = link.get('href', '')
        
        if text.isdigit():
            page_num = int(text)
            if current_page is None or page_num > current_page:
                current_page = page_num
                
            # If we find a link with page number +1, that's our next page
            if page_num == (current_page or 0) + 1:
                return urljoin(current_url, href)
    
    return None

def parse_kvboerse_search(url: str, max_pages: int = MAX_PAGES) -> List[Dict]:
    """
    Enhanced KVBOERSE parser with pagination support.
    """
    print(f"    Using KVBOERSE parser with pagination (max {max_pages} pages)...")
    
    all_items = []
    current_url = url
    pages_scanned = 0
    
    while current_url and pages_scanned < max_pages:
        pages_scanned += 1
        print(f"      Scanning page {pages_scanned}: {current_url}")
        
        try:
            response = requests.get(current_url, headers=HEADERS, timeout=TIMEOUT)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, "lxml")
            
            # Extract job items from current page
            page_items = extract_jobs_from_kvboerse_page(soup, current_url)
            all_items.extend(page_items)
            
            print(f"      Found {len(page_items)} jobs on page {pages_scanned}")
            
            # Look for next page
            next_url = find_next_page(soup, current_url)
            if next_url and next_url != current_url:
                current_url = next_url
                time.sleep(1)  # Be polite between page requests
            else:
                print(f"      No more pages found after {pages_scanned} pages")
                break
                
        except Exception as e:
            print(f"      Error scanning page {pages_scanned}: {e}")
            break
    
    # Deduplicate
    seen = set()
    unique_items = []
    for item in all_items:
        key = item["href"] + "|" + item["title"][:100]
        if key not in seen:
            seen.add(key)
            unique_items.append(item)
    
    print(f"    Total: {len(unique_items)} unique jobs found across {pages_scanned} pages")
    return unique_items

def extract_jobs_from_kvboerse_page(soup: BeautifulSoup, base_url: str) -> List[Dict]:
    """
    Extract job listings from a single KVBOERSE page.
    """
    items = []
    
    # Try multiple selectors for job listings
    job_selectors = [
        '.job-listing', '.job-item', '.stellenangebot',
        '.search-result', '.result-item', '[class*="job"]',
        '.card', '.teaser', '.listing-item',
        'article', '.item', '.entry',
        '.job-teaser', '.stellen-teaser',
    ]
    
    for selector in job_selectors:
        job_elements = soup.select(selector)
        for job in job_elements:
            # Skip elements that are too small
            if len(job.get_text(strip=True)) < 20:
                continue
                
            # Extract title
            title = None
            title_selectors = ['h1', 'h2', 'h3', 'h4', '.title', '.headline', '[class*="title"]']
            for title_sel in title_selectors:
                title_elem = job.select_one(title_sel)
                if title_elem:
                    title = title_elem.get_text(" ", strip=True)
                    if title and len(title) > 5:
                        break
            
            # Extract link
            link_elem = job.select_one('a[href]')
            href = link_elem.get('href') if link_elem else None
            full_url = urljoin(base_url, href) if href else base_url
            
            # If we have a reasonable title, add the item
            if title and len(title) > 5:
                items.append({
                    "href": full_url,
                    "title": title,
                    "source": f"kvboerse_{selector}"
                })
    
    # Fallback: look for any job-like links
    if not items:
        for a in soup.find_all('a', href=True):
            text = a.get_text(" ", strip=True)
            href = a['href']
            
            # Skip obviously non-job links
            if (len(text) < 10 or 
                any(x in href.lower() for x in ['impressum', 'datenschutz', 'login', 'agb']) or
                any(x in text.lower() for x in ['impressum', 'datenschutz'])):
                continue
            
            # If it looks like a job title
            if any(term in text.lower() for term in ['arzt', 'ärztin', 'stellen', 'job', 'psych', 'facharzt']):
                full_url = urljoin(base_url, href)
                items.append({
                    "href": full_url,
                    "title": text,
                    "source": "kvboerse_fallback"
                })
    
    return items

def parse_generic_portal(url: str, max_pages: int = 3) -> List[Dict]:
    """
    Generic portal parser with basic pagination support.
    """
    print(f"    Using generic portal parser (max {max_pages} pages)...")
    
    all_items = []
    current_url = url
    pages_scanned = 0
    
    while current_url and pages_scanned < max_pages:
        pages_scanned += 1
        print(f"      Scanning page {pages_scanned}...")
        
        try:
            response = requests.get(current_url, headers=HEADERS, timeout=TIMEOUT)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, "lxml")
            
            # Extract job items using similar logic as KVBOERSE
            page_items = extract_jobs_from_kvboerse_page(soup, current_url)
            all_items.extend(page_items)
            
            # Look for next page
            next_url = find_next_page(soup, current_url)
            if next_url and next_url != current_url:
                current_url = next_url
                time.sleep(1)
            else:
                break
                
        except Exception as e:
            print(f"      Error scanning page {pages_scanned}: {e}")
            break
    
    # Deduplicate
    seen = set()
    unique_items = []
    for item in all_items:
        key = item["href"] + "|" + item["title"][:100]
        if key not in seen:
            seen.add(key)
            unique_items.append(item)
    
    print(f"    Total: {len(unique_items)} jobs from {pages_scanned} pages")
    return unique_items

def parse_content_page(url: str) -> List[Dict]:
    """
    Parser for regular content pages (single page).
    """
    print(f"    Using content page parser...")
    
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
                    "source": "content_page"
                })
        
        print(f"    Found {len(items)} potential jobs on content page")
        return items
        
    except Exception as e:
        print(f"    Content page parser error: {e}")
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
            candidates = parse_kvboerse_search(url, max_pages=MAX_PAGES)
        elif any(portal in url for portal in ['stellen', 'jobs', 'karriere', 'career']):
            candidates = parse_generic_portal(url, max_pages=3)
        else:
            candidates = parse_content_page(url)
        
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
