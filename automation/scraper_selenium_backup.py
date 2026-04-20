"""
Scraper using Selenium + DuckDuckGo for better JavaScript handling
"""
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager
from bs4 import BeautifulSoup
import time
import re
import pandas as pd
from typing import List, Dict
import random
import urllib.parse

class CompanyScraper:
    def __init__(self):
        # Setup Chrome options
        chrome_options = Options()
        chrome_options.add_argument('--headless')  # Run in background
        chrome_options.add_argument('--no-sandbox')
        chrome_options.add_argument('--disable-dev-shm-usage')
        chrome_options.add_argument('--disable-gpu')
        chrome_options.add_argument('--window-size=1920,1080')
        chrome_options.add_argument('--disable-blink-features=AutomationControlled')
        chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
        chrome_options.add_experimental_option('useAutomationExtension', False)
        chrome_options.add_argument('--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36')
        
        # Initialize Chrome driver
        try:
            print("Initializing Chrome driver...")
            service = Service(ChromeDriverManager().install())
            self.driver = webdriver.Chrome(service=service, options=chrome_options)
            self.driver.implicitly_wait(10)
            print("Chrome driver ready!")
        except Exception as e:
            print(f"Error initializing Chrome: {e}")
            raise
    
    def search_duckduckgo(self, keyword: str, num_results: int = 10) -> List[str]:
        """Search DuckDuckGo and get URLs using Selenium"""
        urls = []
        
        try:
            # DuckDuckGo HTML search
            encoded_keyword = urllib.parse.quote_plus(keyword)
            search_url = f"https://html.duckduckgo.com/html/?q={encoded_keyword}"
            
            print(f"  Searching DuckDuckGo for: {keyword}")
            self.driver.get(search_url)
            time.sleep(2)  # Wait for page load
            
            # Get page source and parse
            soup = BeautifulSoup(self.driver.page_source, 'html.parser')
            
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
                        try:
                            actual_url = urllib.parse.unquote(href.split('uddg=')[1].split('&')[0])
                            if actual_url.startswith('http'):
                                urls.append(actual_url)
                        except:
                            pass
            
            print(f"  Found {len(urls)} URLs")
                
        except Exception as e:
            print(f"  Error searching: {e}")
        
        return list(set(urls))[:num_results]
    
    def extract_company_info(self, url: str) -> Dict:
        """Extract company information from website using Selenium"""
        try:
            print(f"    Visiting: {url[:60]}...")
            
            # Load page with Selenium
            self.driver.set_page_load_timeout(15)
            self.driver.get(url)
            time.sleep(2)  # Wait for JavaScript to load
            
            # Get page source after JavaScript execution
            page_source = self.driver.page_source
            soup = BeautifulSoup(page_source, 'html.parser')
            
            # Company name from title or meta tags
            title = soup.find('title')
            company_name = title.text.strip() if title else url.split('/')[2]
            
            # Try to get better company name from meta tags
            og_title = soup.find('meta', property='og:title')
            if og_title and og_title.get('content'):
                company_name = og_title.get('content').strip()
            
            company_name = company_name.split('|')[0].split('-')[0].strip()[:100]
            
            # Find emails (improved pattern)
            email_pattern = r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}'
            emails = re.findall(email_pattern, page_source)
            # Filter out common fake/example emails
            emails = [e for e in emails if not any(x in e.lower() for x in 
                     ['example', 'test', 'domain', 'email', 'placeholder', 'noreply', 'no-reply', 
                      'donotreply', 'webmaster', 'admin@', 'support@example'])]
            # Prioritize info@ and contact@ emails
            priority_emails = [e for e in emails if any(x in e.lower() for x in ['info@', 'contact@', 'inquiry@'])]
            email = priority_emails[0] if priority_emails else (emails[0] if emails else None)
            
            # Find phone numbers (Japanese and international formats)
            phone_pattern = r'(?:\+81|0)\d{1,4}[-\s]?\d{1,4}[-\s]?\d{4}|\d{2,4}-\d{2,4}-\d{4}'
            phones = re.findall(phone_pattern, page_source)
            phone = phones[0] if phones else None
            
            # Find address (look for common Japanese address patterns)
            address = None
            address_keywords = ['住所', 'address', '所在地', '本社']
            for keyword in address_keywords:
                if keyword in page_source:
                    # Try to extract text near the keyword
                    idx = page_source.find(keyword)
                    if idx != -1:
                        snippet = page_source[idx:idx+200]
                        # Look for Japanese postal code pattern
                        postal_match = re.search(r'〒?\d{3}-?\d{4}', snippet)
                        if postal_match:
                            address = snippet[postal_match.start():postal_match.end()+50].strip()
                            address = re.sub(r'<[^>]+>', '', address)[:200]
                            break
            
            # Find contact form URL
            form_url = None
            for link in soup.find_all('a', href=True):
                href = link.get('href', '').lower()
                text = link.get_text().lower()
                
                if any(kw in href or kw in text for kw in ['contact', 'inquiry', 'form', 'お問い合わせ', '問合せ']):
                    if href.startswith('http'):
                        form_url = href
                    elif href.startswith('/'):
                        base_url = '/'.join(url.split('/')[:3])
                        form_url = base_url + href
                    break
            
            # Extract description from meta tags
            description = None
            meta_desc = soup.find('meta', attrs={'name': 'description'}) or soup.find('meta', property='og:description')
            if meta_desc and meta_desc.get('content'):
                description = meta_desc.get('content').strip()[:300]
            
            print(f"    OK: {company_name[:40]} | Email: {email or 'N/A'} | Phone: {phone or 'N/A'}")
            
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
            print(f"    Error: {str(e)[:80]}")
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
        """Close the browser"""
        try:
            if self.driver:
                self.driver.quit()
                print("Browser closed")
        except Exception as e:
            print(f"Error closing browser: {e}")


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
            print(df[['company_name', 'email', 'phone', 'website']])
            
            df.to_csv("../backend/data/companies.csv", index=False, encoding="utf-8-sig")
            print(f"\n✅ Saved!")
        
    finally:
        scraper.close()
