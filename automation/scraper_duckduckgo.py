"""
Scraper using DuckDuckGo (more scraper-friendly than Google)
"""
import requests
from bs4 import BeautifulSoup
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
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.9',
            'Referer': 'https://duckduckgo.com/'
        })
    
    def search_duckduckgo(self, keyword: str, num_results: int = 10) -> List[str]:
        """Search DuckDuckGo and get URLs"""
        urls = []
        
        try:
            # DuckDuckGo HTML search
            encoded_keyword = urllib.parse.quote_plus(keyword)
            search_url = f"https://html.duckduckgo.com/html/?q={encoded_keyword}"
            
            print(f"  Searching DuckDuckGo for: {keyword}")
            response = self.session.get(search_url, timeout=15)
            
            if response.status_code == 200:
                soup = BeautifulSoup(response.text, 'html.parser')
                
                # Find result links
                for result in soup.find_all('a', class_='result__a'):
                    href = result.get('href', '')
                    if href.startswith('http') and 'duckduckgo.com' not in href:
                        urls.append(href)
                
                # Alternative: find all links with uddg parameter
                if not urls:
                    for link in soup.find_all('a'):
                        href = link.get('href', '')
                        if '//duckduckgo.com/l/?uddg=' in href:
                            # Extract actual URL
                            try:
                                actual_url = urllib.parse.unquote(href.split('uddg=')[1].split('&')[0])
                                if actual_url.startswith('http'):
                                    urls.append(actual_url)
                            except:
                                pass
                
                print(f"  Found {len(urls)} URLs")
            else:
                print(f"  DuckDuckGo returned status: {response.status_code}")
                
        except Exception as e:
            print(f"  Error searching: {e}")
        
        return list(set(urls))[:num_results]
    
    def extract_company_info(self, url: str) -> Dict:
        """Extract company information from website"""
        try:
            print(f"    Visiting: {url[:60]}...")
            
            response = self.session.get(url, timeout=10, allow_redirects=True)
            
            if response.status_code != 200:
                return None
            
            soup = BeautifulSoup(response.text, 'html.parser')
            
            # Company name from title
            title = soup.find('title')
            company_name = title.text.strip() if title else url.split('/')[2]
            company_name = company_name.split('|')[0].split('-')[0].strip()[:100]
            
            # Find emails
            email_pattern = r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}'
            emails = re.findall(email_pattern, response.text)
            emails = [e for e in emails if not any(x in e.lower() for x in ['example', 'test', 'domain', 'email', 'placeholder'])]
            email = emails[0] if emails else None
            
            # Find contact form
            form_url = None
            for link in soup.find_all('a', href=True):
                href = link.get('href', '').lower()
                text = link.get_text().lower()
                
                if any(kw in href or kw in text for kw in ['contact', 'inquiry', 'form']):
                    if href.startswith('http'):
                        form_url = href
                    elif href.startswith('/'):
                        base_url = '/'.join(url.split('/')[:3])
                        form_url = base_url + href
                    break
            
            print(f"    ✓ {company_name[:40]}")
            
            return {
                "company_name": company_name,
                "email": email,
                "website": url,
                "form_url": form_url,
                "address": None,
                "status": "New"
            }
            
        except Exception as e:
            print(f"    Error: {str(e)[:50]}")
            return None
    
    def scrape_companies(self, keywords: List[str], results_per_keyword: int = 5) -> pd.DataFrame:
        """Scrape companies"""
        all_companies = []
        
        for i, keyword in enumerate(keywords, 1):
            print(f"\n[{i}/{len(keywords)}] Keyword: {keyword}")
            
            urls = self.search_duckduckgo(keyword, results_per_keyword)
            
            if not urls:
                print(f"  No URLs found")
                continue
            
            for url in urls:
                company_info = self.extract_company_info(url)
                if company_info:
                    all_companies.append(company_info)
                time.sleep(random.uniform(0.5, 1.5))
            
            time.sleep(random.uniform(1, 2))
        
        if not all_companies:
            print("\n⚠️ No companies found!")
            return pd.DataFrame(columns=['id', 'company_name', 'email', 'website', 'form_url', 'address', 'status', 'memo', 'created_at', 'last_contact'])
        
        df = pd.DataFrame(all_companies)
        df = df.drop_duplicates(subset=['website'])
        
        df['id'] = range(1, len(df) + 1)
        df['memo'] = ''
        df['created_at'] = pd.Timestamp.now().isoformat()
        df['last_contact'] = None
        
        df = df[['id', 'company_name', 'email', 'website', 'form_url', 'address', 'status', 'memo', 'created_at', 'last_contact']]
        
        print(f"\n✅ Collected: {len(df)} companies")
        return df
    
    def close(self):
        self.session.close()


if __name__ == "__main__":
    print("=" * 60)
    print("TESTING DUCKDUCKGO SCRAPER")
    print("=" * 60)
    
    keywords = ["Tokyo real estate"]
    
    scraper = CompanyScraper()
    try:
        df = scraper.scrape_companies(keywords, results_per_keyword=5)
        
        if len(df) > 0:
            print("\nResults:")
            print(df[['company_name', 'email', 'website']])
            
            df.to_csv("../backend/data/companies.csv", index=False, encoding="utf-8-sig")
            print(f"\n✅ Saved!")
        
    finally:
        scraper.close()
