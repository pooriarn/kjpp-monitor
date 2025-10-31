#!/usr/bin/env python3
import requests
from bs4 import BeautifulSoup
import re

def debug_kvboerse():
    url = "https://www.kvboerse.de/suche?q=kinder+jugendpsychiatrie"
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "de-DE,de;q=0.9,en;q=0.8",
        "Accept-Encoding": "gzip, deflate, br",
        "DNT": "1",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Cache-Control": "max-age=0",
    }
    
    print("🔍 Testing KVBOERSE access...")
    print(f"URL: {url}")
    
    try:
        response = requests.get(url, headers=headers, timeout=30)
        print(f"Status Code: {response.status_code}")
        print(f"Content Length: {len(response.text)}")
        print(f"Content Type: {response.headers.get('content-type', 'unknown')}")
        
        # Check if we got blocked
        if response.status_code != 200:
            print("❌ Got non-200 status code")
            print(f"Response: {response.text[:500]}...")
            return
        
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # Check for common blocking indicators
        title = soup.find('title')
        if title:
            print(f"Page Title: {title.get_text()}")
        
        # Check for CAPTCHA or blocking messages
        page_text = soup.get_text().lower()
        if any(word in page_text for word in ['captcha', 'access denied', 'bot', 'blocked', 'cloudflare']):
            print("❌ Page is blocking access (CAPTCHA/bot protection)")
        
        # Look for any job-related content
        print("\n🔎 Searching for job content...")
        
        # Check for common job listing elements
        job_elements = soup.find_all(['h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'a'])
        job_like_elements = []
        
        for elem in job_elements:
            text = elem.get_text(strip=True)
            if not text:
                continue
                
            # Check if it looks like a job title
            if any(term in text.lower() for term in ['arzt', 'ärztin', 'stellen', 'job', 'psych', 'facharzt', 'assistenzarzt']):
                job_like_elements.append(text[:100])
        
        print(f"Found {len(job_like_elements)} job-like elements:")
        for i, job in enumerate(job_like_elements[:10]):  # Show first 10
            print(f"  {i+1}. {job}")
        
        if len(job_like_elements) > 10:
            print(f"  ... and {len(job_like_elements) - 10} more")
        
        # Check for pagination
        pagination = soup.find_all(['a', 'span'], string=re.compile(r'\d+'))
        page_numbers = [p.get_text(strip=True) for p in pagination if p.get_text(strip=True).isdigit()]
        if page_numbers:
            print(f"\n📄 Pagination detected: Pages {min(page_numbers)}-{max(page_numbers)}")
        
        # Save raw HTML for inspection
        with open('debug_kvboerse.html', 'w', encoding='utf-8') as f:
            f.write(response.text)
        print(f"\n💾 Raw HTML saved to: debug_kvboerse.html")
        
        # Check for JavaScript content
        script_tags = soup.find_all('script')
        print(f"\n📜 Found {len(script_tags)} script tags")
        
        # Check for noscript content
        noscript = soup.find('noscript')
        if noscript:
            print("⚠️  Noscript tag found - site may require JavaScript")
            
    except Exception as e:
        print(f"❌ Error: {e}")

if __name__ == "__main__":
    debug_kvboerse()
