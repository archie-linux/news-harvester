import requests
from bs4 import BeautifulSoup
import time
import json
import csv
from datetime import datetime
from urllib.parse import urljoin, urlparse
import logging
from dataclasses import dataclass
from typing import List, Dict, Optional
import re

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

@dataclass
class Article:
    title: str
    url: str
    summary: str
    published_date: str
    source: str
    author: Optional[str] = None

class TechNewsScraper:
    def __init__(self, delay: float = 2.0):
        """
        Initialize scraper with rate limiting delay
        """
        self.delay = delay
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
            'Accept-Encoding': 'gzip, deflate',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1'
        })

    def scrape_site_adaptive(self, site_url: str, max_articles: int = 10) -> List[Article]:
        """
        Adaptive scraping that tries multiple selector strategies
        """
        articles = []
        domain = urlparse(site_url).netloc.replace('www.', '')
        
        try:
            logger.info(f"Fetching {site_url}...")
            response = self.session.get(site_url, timeout=15)
            response.raise_for_status()
            
            soup = BeautifulSoup(response.content, 'html.parser')
            
            # Strategy 1: Look for common article patterns
            articles = self._try_article_selectors(soup, site_url, domain, max_articles)
            
            if not articles:
                # Strategy 2: Look for headline links
                articles = self._try_headline_selectors(soup, site_url, domain, max_articles)
            
            if not articles:
                # Strategy 3: Look for any links that might be articles
                articles = self._try_generic_link_patterns(soup, site_url, domain, max_articles)
            
            logger.info(f"Scraped {len(articles)} articles from {domain}")
            
        except Exception as e:
            logger.error(f"Error scraping {site_url}: {e}")
        
        time.sleep(self.delay)
        return articles

    def _try_article_selectors(self, soup, site_url, domain, max_articles):
        """Try common article container selectors"""
        articles = []
        
        article_selectors = [
            'article',
            '.post', '.entry', '.story',
            '[class*="article"]', '[class*="post"]', '[class*="story"]',
            '.content-item', '.feed-item', '.news-item'
        ]
        
        for selector in article_selectors:
            elements = soup.select(selector)
            if len(elements) >= 3:  # Only proceed if we find multiple elements
                logger.debug(f"Trying article selector: {selector} (found {len(elements)} elements)")
                
                for element in elements[:max_articles]:
                    article = self._extract_article_from_element(element, site_url, domain)
                    if article:
                        articles.append(article)
                
                if articles:
                    break
        
        return articles

    def _try_headline_selectors(self, soup, site_url, domain, max_articles):
        """Try headline-specific selectors"""
        articles = []
        
        headline_selectors = [
            'h1 a[href]', 'h2 a[href]', 'h3 a[href]',
            '.headline a', '.title a', '.entry-title a',
            '[class*="headline"] a', '[class*="title"] a',
            'a[href*="/2024/"]', 'a[href*="/2025/"]',  # Year-based URLs
        ]
        
        for selector in headline_selectors:
            links = soup.select(selector)
            if len(links) >= 3:
                logger.debug(f"Trying headline selector: {selector} (found {len(links)} links)")
                
                for link in links[:max_articles]:
                    article = self._create_article_from_link(link, site_url, domain)
                    if article and self._is_valid_article_url(article.url):
                        articles.append(article)
                
                if len(articles) >= 3:  # Good enough
                    break
        
        return articles

    def _try_generic_link_patterns(self, soup, site_url, domain, max_articles):
        """Try to find article links using generic patterns"""
        articles = []
        
        # Look for links that seem like articles
        all_links = soup.find_all('a', href=True)
        
        for link in all_links:
            if len(articles) >= max_articles:
                break
                
            href = link.get('href', '')
            text = link.get_text(strip=True)
            
            # Skip if no meaningful text
            if not text or len(text) < 10:
                continue
            
            # Skip navigation links
            if any(skip in text.lower() for skip in ['home', 'about', 'contact', 'subscribe', 'login', 'menu']):
                continue
            
            # Look for article-like URLs
            if self._looks_like_article_url(href) and self._is_valid_article_url(href):
                full_url = urljoin(site_url, href) if not href.startswith('http') else href
                
                article = Article(
                    title=text[:100] + "..." if len(text) > 100 else text,
                    url=full_url,
                    summary="",
                    published_date=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    source=domain
                )
                articles.append(article)
        
        return articles

    def _extract_article_from_element(self, element, site_url, domain):
        """Extract article info from a container element"""
        # Look for title/link
        title_selectors = [
            'h1 a', 'h2 a', 'h3 a', 'h4 a',
            '.title a', '.headline a', '.entry-title a',
            'a[href]'  # fallback
        ]
        
        link_elem = None
        for selector in title_selectors:
            link_elem = element.select_one(selector)
            if link_elem:
                break
        
        if not link_elem:
            return None
        
        title = link_elem.get_text(strip=True)
        href = link_elem.get('href', '')
        
        if not title or not href:
            return None
        
        full_url = urljoin(site_url, href) if not href.startswith('http') else href
        
        # Look for summary
        summary_selectors = [
            '.excerpt', '.summary', '.description', '.intro',
            'p', '.content'
        ]
        
        summary = ""
        for selector in summary_selectors:
            summary_elem = element.select_one(selector)
            if summary_elem:
                summary = summary_elem.get_text(strip=True)
                if len(summary) > 20:  # Only use if substantial
                    break
        
        # Look for author
        author_selectors = [
            '.author', '.byline', '.writer', '[class*="author"]'
        ]
        
        author = None
        for selector in author_selectors:
            author_elem = element.select_one(selector)
            if author_elem:
                author = author_elem.get_text(strip=True)
                break
        
        return Article(
            title=title,
            url=full_url,
            summary=summary[:200] + "..." if len(summary) > 200 else summary,
            published_date=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            source=domain,
            author=author
        )

    def _create_article_from_link(self, link, site_url, domain):
        """Create article from a link element"""
        title = link.get_text(strip=True)
        href = link.get('href', '')
        
        if not title or not href:
            return None
        
        full_url = urljoin(site_url, href) if not href.startswith('http') else href
        
        # Try to find summary from nearby elements
        summary = ""
        parent = link.parent
        if parent:
            # Look for text in parent or sibling elements
            for elem in parent.find_all(['p', 'div', 'span'], limit=3):
                text = elem.get_text(strip=True)
                if text and len(text) > 20 and text != title:
                    summary = text
                    break
        
        return Article(
            title=title,
            url=full_url,
            summary=summary[:200] + "..." if len(summary) > 200 else summary,
            published_date=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            source=domain
        )

    def _looks_like_article_url(self, url):
        """Check if URL looks like an article"""
        if not url:
            return False
        
        article_patterns = [
            r'/\d{4}/',  # Contains year
            r'/article/',
            r'/post/',
            r'/story/',
            r'/news/',
            r'/blog/',
            r'\.html',
            r'-\d+$',  # Ends with dash and numbers
        ]
        
        return any(re.search(pattern, url, re.IGNORECASE) for pattern in article_patterns)

    def _is_valid_article_url(self, url):
        """Check if URL is a valid article (not navigation, etc.)"""
        if not url:
            return False
        
        # Skip certain types of links
        skip_patterns = [
            r'#', r'javascript:', r'mailto:',
            r'/tag/', r'/category/', r'/author/',
            r'/search', r'/login', r'/register',
            r'\.pdf$', r'\.jpg$', r'\.png$', r'\.gif$'
        ]
        
        return not any(re.search(pattern, url, re.IGNORECASE) for pattern in skip_patterns)

    def scrape_all_sites(self, max_articles_per_site: int = 5) -> Dict[str, List[Article]]:
        """
        Scrape all configured tech news sites
        """
        sites = [
            'https://techcrunch.com',
            'https://www.theverge.com',
            'https://www.wired.com',
            'https://arstechnica.com',
            'https://www.zdnet.com',
            'https://thenextweb.com',
            'https://gizmodo.com',
            'https://www.cnet.com',
            'https://www.techradar.com',
            'https://www.digitaltrends.com'
        ]
        
        results = {}
        
        for site in sites:
            logger.info(f"Scraping {site}...")
            articles = self.scrape_site_adaptive(site, max_articles_per_site)
            domain = urlparse(site).netloc.replace('www.', '')
            results[domain] = articles
            
            # Add some variety in delays to be more natural
            time.sleep(self.delay + (time.time() % 1))
        
        return results

    def print_debug_info(self, site_url: str):
        """
        Debug function to help understand site structure
        """
        try:
            response = self.session.get(site_url, timeout=15)
            soup = BeautifulSoup(response.content, 'html.parser')
            
            print(f"\n=== DEBUG INFO FOR {site_url} ===")
            
            # Count different element types
            articles = soup.find_all('article')
            h2_links = soup.select('h2 a[href]')
            h3_links = soup.select('h3 a[href]')
            all_links = soup.find_all('a', href=True)
            
            print(f"Articles found: {len(articles)}")
            print(f"H2 links found: {len(h2_links)}")
            print(f"H3 links found: {len(h3_links)}")
            print(f"Total links found: {len(all_links)}")
            
            # Sample some link texts
            print("\nSample link texts:")
            for i, link in enumerate(all_links[:10]):
                text = link.get_text(strip=True)
                href = link.get('href', '')
                if text and len(text) > 10:
                    print(f"  {i+1}. {text[:60]}... -> {href[:50]}...")
            
        except Exception as e:
            print(f"Debug error: {e}")

    def save_to_json(self, articles_dict: Dict[str, List[Article]], filename: str = None):
        """Save articles to JSON file"""
        if filename is None:
            filename = f"tech_news_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        
        json_data = {}
        for site, articles in articles_dict.items():
            json_data[site] = [
                {
                    'title': article.title,
                    'url': article.url,
                    'summary': article.summary,
                    'published_date': article.published_date,
                    'source': article.source,
                    'author': article.author
                }
                for article in articles
            ]
        
        with open(filename, 'w', encoding='utf-8') as f:
            json.dump(json_data, f, indent=2, ensure_ascii=False)
        
        logger.info(f"Articles saved to {filename}")
        return filename

    def save_to_csv(self, articles_dict: Dict[str, List[Article]], filename: str = None):
        """Save articles to CSV file"""
        if filename is None:
            filename = f"tech_news_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        
        all_articles = []
        for articles in articles_dict.values():
            all_articles.extend(articles)
        
        with open(filename, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow(['Title', 'URL', 'Summary', 'Published Date', 'Source', 'Author'])
            
            for article in all_articles:
                writer.writerow([
                    article.title,
                    article.url,
                    article.summary,
                    article.published_date,
                    article.source,
                    article.author
                ])
        
        logger.info(f"Articles saved to {filename}")
        return filename

def main():
    """
    Main function to run the scraper
    """
    # Initialize scraper with 2 second delay between requests
    scraper = TechNewsScraper(delay=2.0)
    
    print("Starting adaptive tech news scraping...")
    print("This version tries multiple strategies to find articles on each site.")
    
    # Scrape all sites
    all_articles = scraper.scrape_all_sites(max_articles_per_site=5)
    
    # Display results
    total_articles = sum(len(articles) for articles in all_articles.values())
    print(f"\n{'='*50}")
    print(f"SCRAPING RESULTS")
    print(f"{'='*50}")
    print(f"Total articles scraped: {total_articles}")
    
    for site, articles in all_articles.items():
        print(f"\n{site}: {len(articles)} articles")
        for i, article in enumerate(articles[:3], 1):  # Show first 3
            print(f"  {i}. {article.title}")
            if article.summary:
                print(f"     Summary: {article.summary[:100]}...")
    
    if total_articles > 0:
        # Save results
        json_file = scraper.save_to_json(all_articles)
        csv_file = scraper.save_to_csv(all_articles)
        
        print(f"\nFiles saved:")
        print(f"- {json_file}")
        print(f"- {csv_file}")
    else:
        print("\nNo articles were scraped. Sites may be blocking requests or have changed structure.")
        print("Try running the debug function on individual sites:")
        print("scraper.print_debug_info('https://techcrunch.com')")
    
    print("\nScraping completed!")

def debug_single_site(site_url: str):
    """
    Debug function to analyze a single site
    """
    scraper = TechNewsScraper()
    scraper.print_debug_info(site_url)
    
    print(f"\nTrying to scrape {site_url}...")
    articles = scraper.scrape_site_adaptive(site_url, 5)
    
    print(f"Found {len(articles)} articles:")
    for i, article in enumerate(articles, 1):
        print(f"{i}. {article.title}")
        print(f"   URL: {article.url}")
        if article.summary:
            print(f"   Summary: {article.summary[:100]}...")
        print()

if __name__ == "__main__":
    main()
    
    # Uncomment to debug specific sites:
    # debug_single_site('https://techcrunch.com')
    # debug_single_site('https://www.theverge.com')
