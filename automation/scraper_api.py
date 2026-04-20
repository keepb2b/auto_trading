"""
Scraper using public APIs and business directories
More reliable than search engines
"""
from bs4 import BeautifulSoup
import requests
import time
import re
import pandas as pd
from typing import List, Dict
import random
import urllib.parse

class CompanyScraper:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        })
        print("✅ Scraper initialized")
    
    def search_companies(self, keyword: str, num_results: int = 10) -> List[str]:
        """Generate URLs to scrape based on keyword"""
        urls = []
        
        print(f"  Generating URLs for: {keyword}")
        
        # Parse keyword to extract location and industry
        words = keyword.lower().split()
        location = ""
        industry = ""
        
        # Common locations
        locations = ['tokyo', '東京', 'osaka', '大阪', 'kyoto', '京都', 'yokohama', '横浜', 
                    'nagoya', '名古屋', 'fukuoka', '福岡', 'sapporo', '札幌']
        
        for word in words:
            if any(loc in word for loc in locations):
                location = word
            else:
                industry += word + " "
        
        industry = industry.strip()
        
        # Generate URLs from known business directories and platforms
        base_urls = [
            # Japanese business directories
            f"https://www.hotpepper.jp/",
            f"https://tabelog.com/",
            f"https://www.gnavi.co.jp/",
            f"https://retty.me/",
            
            # International directories
            f"https://www.tripadvisor.com/",
            f"https://www.yelp.com/",
            f"https://www.timeout.com/tokyo/restaurants",
            
            # Generic company sites (common patterns)
            f"https://{industry.replace(' ', '-')}.co.jp",
            f"https://{industry.replace(' ', '')}.jp",
            f"https://www.{industry.replace(' ', '-')}.com",
        ]
        
        # Try to actually search and get real results
        try:
            # Use a simple search API that doesn't block
            encoded = urllib.parse.quote_plus(keyword)
            
            # Try Brave Search (more API-friendly)
            print("  Trying Brave Search...")
            brave_url = f"https://search.brave.com/search?q={encoded}"
            response = self.session.get(brave_url, timeout=10)
            
            if response.status_code == 200:
                soup = BeautifulSoup(response.text, 'html.parser')
                
                # Find result snippets
                for result in soup.find_all('div', class_='snippet'):
                    link = result.find_parent().find('a', href=True)
                    if link:
                        href = link.get('href', '')
                        if href.startswith('http') and 'brave.com' not in href:
                            urls.append(href)
                
                # Also try finding any external links
                if len(urls) < 5:
                    for link in soup.find_all('a', href=True):
                        href = link.get('href', '')
                        if href.startswith('http') and 'brave.com' not in href and 'search' not in href:
                            urls.append(href)
                
                print(f"  Brave found {len(urls)} URLs")
        except Exception as e:
            print(f"  Brave search failed: {str(e)[:50]}")
        
        # If still no URLs, use directory URLs
        if len(urls) < 3:
            print("  Using business directories...")
            urls.extend(base_urls)
        
        # Clean URLs
        clean_urls = []
        seen = set()
        skip_domains = ['youtube.com', 'facebook.com', 'twitter.com', 'instagram.com',
                       'wikipedia.org', 'amazon.com', 'google.com', 'bing.com', 'brave.com']
        
        for url in urls:
            if url not in seen and not any(domain in url.lower() for domain in skip_domains):
                clean_urls.append(url)
                seen.add(url)
        
        result_urls = clean_urls[:num_results]
        
        print(f"  ✅ Generated {len(result_urls)} URLs to scrape")
        if result_urls:
            for i, url in enumerate(result_urls[:3], 1):
                print(f"     [{i}] {url[:65]}...")
        
        return result_urls
    
    def extract_company_info(self, url: str) -> Dict:
        """Extract company information from website"""
        try:
            print(f"    Visiting: {url[:60]}...")
            
            response = self.session.get(url, timeout=10, allow_redirects=True)
            
            if response.status_code != 200:
                print(f"    Status {response.status_code}")
                return None
            
            soup = BeautifulSoup(response.text, 'html.parser')
            page_text = response.text
            
            # Company name
            title = soup.find('title')
            company_name = title.text.strip() if title else url.split('/')[2]
            og_title = soup.find('meta', property='og:title')
            if og_title and og_title.get('content'):
                company_name = og_title.get('content').strip()
            company_name = company_name.split('|')[0].split('-')[0].strip()[:100]
            
            # Email
            email_pattern = r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}'
            emails = re.findall(email_pattern, page_text)
            emails = [e for e in emails if not any(x in e.lower() for x in 
                     ['example', 'test', 'domain', 'placeholder', 'noreply', 'no-reply'])]
            priority_emails = [e for e in emails if any(x in e.lower() for x in ['info@', 'contact@', 'inquiry@'])]
            email = priority_emails[0] if priority_emails else (emails[0] if emails else None)
            
            # Phone
            phone_pattern = r'(?:\+81|0)\d{1,4}[-\s]?\d{1,4}[-\s]?\d{4}|\d{2,4}-\d{2,4}-\d{4}'
            phones = re.findall(phone_pattern, page_text)
            phone = phones[0] if phones else None
            
            # Address
            address = None
            for keyword in ['住所', 'address', '所在地', '本社']:
                if keyword in page_text:
                    idx = page_text.find(keyword)
                    if idx != -1:
                        snippet = page_text[idx:idx+200]
                        postal_match = re.search(r'〒?\d{3}-?\d{4}', snippet)
                        if postal_match:
                            address = snippet[postal_match.start():postal_match.end()+50].strip()
                            address = re.sub(r'<[^>]+>', '', address)[:200]
                            break
            
            # Contact form
            form_url = None
            for link in soup.find_all('a', href=True):
                href = link.get('href', '').lower()
                text = link.get_text().lower()
                if any(kw in href or kw in text for kw in ['contact', 'inquiry', 'form', 'お問い合わせ']):
                    if href.startswith('http'):
                        form_url = href
                    elif href.startswith('/'):
                        base_url = '/'.join(url.split('/')[:3])
                        form_url = base_url + href
                    break
            
            # Description
            description = None
            meta_desc = soup.find('meta', attrs={'name': 'description'}) or soup.find('meta', property='og:description')
            if meta_desc and meta_desc.get('content'):
                description = meta_desc.get('content').strip()[:300]
            
            print(f"    ✅ {company_name[:40]} | Email: {email or 'N/A'} | Phone: {phone or 'N/A'}")
            
            return {
                "company_name": company_name,
                "email": email,
                "phone": phone,
                "website": url,
                "form_url": form_url,
                "address": address,
                "description": description,
                "status": "New"
            }
            
        except Exception as e:
            print(f"    ❌ Error: {str(e)[:80]}")
            return None
    
    def scrape_companies(self, keywords: List[str], results_per_keyword: int = 5) -> pd.DataFrame:
        """Scrape companies"""
        all_companies = []
        
        for i, keyword in enumerate(keywords, 1):
            print(f"\n[{i}/{len(keywords)}] Keyword: {keyword}")
            
            urls = self.search_companies(keyword, results_per_keyword)
            
            if not urls:
                print(f"  ⚠️ No URLs generated")
                continue
            
            print(f"  Scraping {len(urls)} URLs...")
            
            for j, url in enumerate(urls, 1):
                print(f"  [{j}/{len(urls)}]", end=" ")
                company_info = self.extract_company_info(url)
                if company_info:
                    all_companies.append(company_info)
                time.sleep(random.uniform(1, 2))
            
            time.sleep(random.uniform(1, 2))
        
        if not all_companies:
            print("\n⚠️ No companies found!")
            return pd.DataFrame(columns=['id', 'company_name', 'email', 'phone', 'website', 'form_url', 'address', 'description', 'status', 'memo', 'created_at', 'last_contact'])
        
        df = pd.DataFrame(all_companies)
        df = df.drop_duplicates(subset=['website'])
        
        df['id'] = range(1, len(df) + 1)
        df['memo'] = ''
        df['created_at'] = pd.Timestamp.now().isoformat()
        df['last_contact'] = None
        
        df = df[['id', 'company_name', 'email', 'phone', 'website', 'form_url', 'address', 'description', 'status', 'memo', 'created_at', 'last_contact']]
        
        print(f"\n✅ Collected: {len(df)} companies")
        return df
    
    def close(self):
        """Close session"""
        self.session.close()


if __name__ == "__main__":
    print("=" * 60)
    print("TESTING API SCRAPER")
    print("=" * 60)
    
    keywords = ["Tokyo restaurant"]
    
    scraper = CompanyScraper()
    try:
        df = scraper.scrape_companies(keywords, results_per_keyword=5)
        
        if len(df) > 0:
            print("\nResults:")
            for idx, row in df.iterrows():
                print(f"\n[{idx+1}] {row['company_name']}")
                print(f"    Email: {row['email'] or 'N/A'}")
                print(f"    Phone: {row['phone'] or 'N/A'}")
                print(f"    Website: {row['website']}")
            
            df.to_csv("../backend/data/companies.csv", index=False, encoding="utf-8-sig")
            print(f"\n✅ Saved!")
        
    finally:
        scraper.close()
