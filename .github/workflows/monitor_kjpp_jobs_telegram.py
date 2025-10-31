#!/usr/bin/env python3
low = href.lower()
if any(w in low for w in BLOCK_PATH_WORDS): return False
if any(p in low for p in ["/stellen", "/jobs", "/karriere", "/bewerb", "/stellenangebot", "/ausschreibung", "vacanc", "job", "position", "bewerb", "medizin", "arzt", "psycholog"]):
return True
return bool(KEYWORDS_RE.search(text or ""))

def extract_candidates(html, base_url):
soup = BeautifulSoup(html, "lxml")
items = []
for a in soup.find_all("a"):
href = normalize(base_url, a.get("href"))
text = (a.get_text(" ", strip=True) or "").strip()
if href.startswith(("http://", "https://")) and (looks_like_job_link(href, text) or KEYWORDS_RE.search(text)):
items.append({"href": href, "title": text or href})
# Fallback: Keywords im Seitentext
if KEYWORDS_RE.search(soup.get_text(" ", strip=True)):
items.append({"href": base_url, "title": "Hinweis: Keywords auf Seite gefunden"})
# Dedupe
seen, uniq = set(), []
for it in items:
k = it["href"].strip()
if k not in seen:
seen.add(k); uniq.append(it)
return uniq

def make_id(url, title):
return hashlib.sha256((url + "|" + (title or "")).encode("utf-8")).hexdigest()[:24]

def tgsend(text):
if not TG_TOKEN or not TG_CHAT:
print("[WARN] Telegram nicht konfiguriert"); return
# Telegram-Message-Limit ~4096 Zeichen → ggf. splitten
chunks = [text[i:i+3500] for i in range(0, len(text), 3500)] or [text]
for c in chunks:
try:
requests.post(f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
json={"chat_id": TG_CHAT, "text": c, "disable_web_page_preview": True},
timeout=30)
except Exception as e:
print("[WARN] Telegram send:", e)

def run_once():
if not TG_TOKEN or not TG_CHAT:
raise SystemExit("Bitte TELEGRAM_BOT_TOKEN und TELEGRAM_CHAT_ID als Umgebungsvariablen setzen.")
if not Path(URLS_FILE).exists():
raise SystemExit(f"{URLS_FILE} fehlt.")
urls = [u.strip() for u in Path(URLS_FILE).read_text(encoding="utf-8").splitlines() if u.strip() and not u.strip().startswith("#")]
state = load_state()
new_lines = []

for url in urls:
try:
html = http_get(url)
for c in extract_candidates(html, url):
txt = f"{c.get('title','')} {c['href']}"
if KEYWORDS_RE.search(txt):
uid = make_id(c["href"], c.get("title",""))
if uid not in state:
state[uid] = time.time()
new_lines.append(f"• {c.get('title','(ohne Titel)')}\n {c['href']}")
except Exception as e:
new_lines.append(f"[WARN] {url}: {e}")
time.sleep(SLEEP_BETWEEN)

if new_lines:
tgsend("🆕 Neue KJPP-Stellen:\n\n" + "\n".join(new_lines))

save_state(state)

if __name__ == "__main__":
run_once()
print("OK")
