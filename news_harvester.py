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
import os

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

    def scrape_all_sites(self, max_articles_per_site: int = 5, site_type: str = "tech") -> Dict[str, List[Article]]:
        """
        Scrape all configured news sites for the specified type
        """
        if site_type == "tech":
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
        elif site_type == "security":
            sites = [
                'https://thehackernews.com',
                'https://www.csoonline.com',
                'https://www.darkreading.com',
                'https://www.securityweek.com',
                'https://www.infosecurity-magazine.com',
                'https://krebsonsecurity.com',
                'https://threatpost.com',
                'https://www.bleepingcomputer.com',
                'https://grahamcluley.com',
                'https://securitytrails.com/blog',
                'https://nakedsecurity.sophos.com',
                'https://www.schneier.com',
                'https://www.tripwire.com/state-of-security',
                'https://cyberscoop.com',
                'https://www.helpnetsecurity.com'
            ]
        elif site_type == "robotics":
            sites = [
                'https://www.therobotreport.com',
                'https://robohub.org',
                'https://spectrum.ieee.org',
                'https://roboticsandautomationnews.com',
                'https://www.science.org/journal/scirobotics',
                'https://www.robotics247.com',
                'https://www.roboticstomorrow.com',
                'https://news.mit.edu/topic/robotics',
                'https://www.iotworldtoday.com',
                'https://www.nasa.gov/news/robotics'
            ]
        elif site_type == "linux":
            sites = [
                'https://lwn.net',
                'https://www.phoronix.com',
                'https://www.linuxtoday.com',
                'https://www.linuxjournal.com',
                'https://www.omgubuntu.co.uk',
                'https://www.linux.com',
                'https://distrowatch.com',
                'https://linux.slashdot.org',
                'https://www.tecmint.com',
                'https://www.unixmen.com',
                'https://www.xmodulo.com'
            ]
        else:
            raise ValueError(f"Unknown site_type: {site_type}. Supported types: tech, security, robotics, linux")
        
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

    def save_to_json(self, articles_dict: Dict[str, List[Article]], filename: str = None, file_prefix: str = "tech_news"):
        """Save articles to JSON file"""
        # Ensure json directory exists
        os.makedirs('json', exist_ok=True)
        
        if filename is None:
            filename = f"json/{file_prefix}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        else:
            # If filename is provided without path, add json folder
            if not filename.startswith('json/'):
                filename = f"json/{filename}"
        
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

    def save_to_csv(self, articles_dict: Dict[str, List[Article]], filename: str = None, file_prefix: str = "tech_news"):
        """Save articles to CSV file"""
        # Ensure csv directory exists
        os.makedirs('csv', exist_ok=True)
        
        if filename is None:
            filename = f"csv/{file_prefix}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        else:
            # If filename is provided without path, add csv folder
            if not filename.startswith('csv/'):
                filename = f"csv/{filename}"
        
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

    def load_html_template(self, template_file: str = "news_template.html"):
        """Load HTML template from external file"""
        try:
            with open(template_file, 'r', encoding='utf-8') as f:
                return f.read()
        except FileNotFoundError:
            logger.warning(f"Template file {template_file} not found. Using default template.")
            return self._get_default_html_template()
        except Exception as e:
            logger.error(f"Error loading template {template_file}: {e}")
            return self._get_default_html_template()


    def _get_default_html_template(self):
        """Return default HTML template as fallback"""
        return """<!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>{title}</title>
        <style>
            * {{ margin: 0; padding: 0; box-sizing: border-box; }}
            body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; line-height: 1.6; color: #333; background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); min-height: 100vh; padding: 20px; }}
            .container {{ max-width: 1200px; margin: 0 auto; background: white; border-radius: 20px; box-shadow: 0 20px 60px rgba(0,0,0,0.1); overflow: hidden; }}
            .header {{ background: linear-gradient(135deg, #1e3c72 0%, #2a5298 100%); color: white; padding: 40px; text-align: center; }}
            .header.robotics-theme {{ background: linear-gradient(135deg, #ff6b6b 0%, #ee5a52 100%); }}
            .header.linux-theme {{ background: linear-gradient(135deg, #f39c12 0%, #d68910 100%); }}
            .header.security-theme {{ background: linear-gradient(135deg, #e74c3c 0%, #c0392b 100%); }}
            .header.combined-theme {{ background: linear-gradient(135deg, #9b59b6 0%, #8e44ad 100%); }}
            .header h1 {{ font-size: 2.5rem; margin-bottom: 10px; font-weight: 700; }}
            .header .subtitle {{ font-size: 1.2rem; opacity: 0.9; margin-bottom: 20px; }}
            .stats {{ display: flex; justify-content: center; gap: 30px; margin-top: 20px; }}
            .stat-item {{ text-align: center; }}
            .stat-number {{ font-size: 2rem; font-weight: bold; display: block; }}
            .stat-label {{ font-size: 0.9rem; opacity: 0.8; }}
            .content {{ padding: 40px; }}
            .site-section {{ margin-bottom: 50px; }}
            .site-header {{ display: flex; align-items: center; margin-bottom: 25px; padding-bottom: 15px; border-bottom: 3px solid #f0f0f0; }}
            .site-icon {{ width: 40px; height: 40px; border-radius: 50%; background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); display: flex; align-items: center; justify-content: center; color: white; font-weight: bold; margin-right: 15px; font-size: 1.2rem; }}
            .robotics-prefix .site-icon {{ background: linear-gradient(135deg, #ff6b6b 0%, #ee5a52 100%); }}
            .linux-prefix .site-icon {{ background: linear-gradient(135deg, #f39c12 0%, #d68910 100%); }}
            .security-prefix .site-icon {{ background: linear-gradient(135deg, #e74c3c 0%, #c0392b 100%); }}
            .site-name {{ font-size: 1.8rem; font-weight: 600; color: #2c3e50; flex: 1; }}
            .article-count {{ background: #e74c3c; color: white; padding: 8px 16px; border-radius: 20px; font-size: 0.9rem; font-weight: 600; }}
            .articles-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(350px, 1fr)); gap: 25px; }}
            .article-card {{ background: #fff; border: 1px solid #e0e0e0; border-radius: 15px; padding: 25px; transition: all 0.3s ease; position: relative; overflow: hidden; }}
            .article-card:hover {{ transform: translateY(-5px); box-shadow: 0 15px 40px rgba(0,0,0,0.1); border-color: #667eea; }}
            .article-card::before {{ content: ''; position: absolute; top: 0; left: 0; right: 0; height: 4px; background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); }}
            .robotics-prefix .article-card::before {{ background: linear-gradient(135deg, #ff6b6b 0%, #ee5a52 100%); }}
            .linux-prefix .article-card::before {{ background: linear-gradient(135deg, #f39c12 0%, #d68910 100%); }}
            .security-prefix .article-card::before {{ background: linear-gradient(135deg, #e74c3c 0%, #c0392b 100%); }}
            .article-title {{ font-size: 1.3rem; font-weight: 600; color: #2c3e50; margin-bottom: 12px; line-height: 1.4; }}
            .article-title a {{ color: inherit; text-decoration: none; transition: color 0.3s ease; }}
            .article-title a:hover {{ color: #667eea; }}
            .article-summary {{ color: #666; margin-bottom: 15px; font-size: 0.95rem; line-height: 1.5; }}
            .article-meta {{ display: flex; justify-content: space-between; align-items: center; font-size: 0.85rem; color: #999; border-top: 1px solid #f0f0f0; padding-top: 15px; }}
            .article-author {{ font-weight: 500; color: #667eea; }}
            .article-date {{ opacity: 0.8; }}
            .no-articles {{ text-align: center; color: #999; font-style: italic; padding: 40px; background: #f9f9f9; border-radius: 10px; }}
            .footer {{ background: #f8f9fa; padding: 30px; text-align: center; color: #666; border-top: 1px solid #e0e0e0; }}
            .footer p {{ margin-bottom: 10px; }}
            .footer .timestamp {{ font-weight: 600; color: #333; }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header {theme_class}">
                <h1>{header_title}</h1>
                <div class="subtitle">{subtitle}</div>
                <div class="stats">
                    <div class="stat-item">
                        <span class="stat-number">{total_articles}</span>
                        <span class="stat-label">Total Articles</span>
                    </div>
                    <div class="stat-item">
                        <span class="stat-number">{total_sources}</span>
                        <span class="stat-label">Sources</span>
                    </div>
                    <div class="stat-item">
                        <span class="stat-number">{generation_time}</span>
                        <span class="stat-label">Generated</span>
                    </div>
                </div>
            </div>
            
            <div class="content">
                {content}
            </div>
            
            <div class="footer">
                <p>Generated by Enhanced Tech News Harvester</p>
                <p class="timestamp">Created on {timestamp}</p>
                <p>Click on article titles to read the full stories</p>
            </div>
        </div>
    </body>
    </html>"""

    def ensure_output_directories(self):
        """Create output directories if they don't exist"""
        directories = ['html', 'json', 'csv']
        for directory in directories:
            os.makedirs(directory, exist_ok=True)


    def save_to_html(self, articles_dict: Dict[str, List[Article]], filename: str = None, 
                    file_prefix: str = "tech_news", template_file: str = "news_template.html",
                    site_type: str = "tech"):
        """Save articles to HTML file with styling using external template"""
        # Ensure html directory exists
        os.makedirs('html', exist_ok=True)
        
        if filename is None:
            filename = f"html/{file_prefix}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.html"
        else:
            # If filename is provided without path, add html folder
            if not filename.startswith('html/'):
                filename = f"html/{filename}"
        
        total_articles = sum(len(articles) for articles in articles_dict.values())
        
        # Load template
        template = self.load_html_template(template_file)
        
        # Generate content for each site
        content = ""
        for site, articles in articles_dict.items():
            # Handle prefixed site names for combined reports
            if site.startswith(('tech_', 'security_', 'robotics_', 'linux_')):
                prefix, actual_site = site.split('_', 1)
                site_display = actual_site.replace('.com', '').replace('.', ' ').title()
                site_class = f"{prefix}-prefix"
                
                # Add appropriate emoji based on prefix
                emoji_map = {
                    "tech": "🚀",
                    "security": "🔒", 
                    "robotics": "🤖",
                    "linux": "🐧"
                }
                site_display = f"{emoji_map.get(prefix, '📰')} {site_display}"
            else:
                actual_site = site
                site_display = site.replace('.com', '').replace('.', ' ').title()
                site_class = ""
                
                # Add emoji based on site type
                emoji_map = {
                    "tech": "🚀",
                    "security": "🔒",
                    "robotics": "🤖", 
                    "linux": "🐧"
                }
                site_display = f"{emoji_map.get(site_type, '📰')} {site_display}"
            
            site_initial = actual_site[0].upper()
            
            content += f"""
            <div class="site-section {site_class}">
                <div class="site-header">
                    <div class="site-icon">{site_initial}</div>
                    <h2 class="site-name">{site_display}</h2>
                    <div class="article-count">{len(articles)} articles</div>
                </div>
    """
            
            if articles:
                content += '<div class="articles-grid">'
                
                for article in articles:
                    # Clean and truncate summary
                    summary = article.summary.replace('\n', ' ').strip()
                    if not summary:
                        summary = "No summary available."
                    
                    # Format author display
                    author_display = f'By {article.author}' if article.author else 'Unknown Author'
                    
                    # Format date
                    try:
                        date_obj = datetime.strptime(article.published_date, '%Y-%m-%d %H:%M:%S')
                        formatted_date = date_obj.strftime('%b %d, %Y at %H:%M')
                    except:
                        formatted_date = article.published_date
                    
                    content += f"""
                    <div class="article-card">
                        <h3 class="article-title">
                            <a href="{article.url}" target="_blank" rel="noopener noreferrer">
                                {article.title}
                            </a>
                        </h3>
                        <div class="article-summary">{summary}</div>
                        <div class="article-meta">
                            <span class="article-author">{author_display}</span>
                            <span class="article-date">{formatted_date}</span>
                        </div>
                    </div>
    """
                
                content += '</div>'
            else:
                content += '<div class="no-articles">No articles found for this source.</div>'
            
            content += '</div>'
        
        # Set template variables based on site type
        theme_config = {
            "tech": {
                "header_title": "🚀 Tech News",
                "subtitle": "Latest articles from top technology news sources",
                "theme_class": "tech-theme"
            },
            "security": {
                "header_title": "🔒 Cybersecurity News",
                "subtitle": "Latest cybersecurity articles from top security news sources",
                "theme_class": "security-theme"
            },
            "robotics": {
                "header_title": "🤖 Robotics News",
                "subtitle": "Latest robotics articles from top robotics and automation sources",
                "theme_class": "robotics-theme"
            },
            "linux": {
                "header_title": "🐧 Linux News",
                "subtitle": "Latest Linux and open source articles from top Linux news sources",
                "theme_class": "linux-theme"
            },
            "combined": {
                "header_title": "🔄 Combined News Report",
                "subtitle": "Latest articles from multiple technology news categories",
                "theme_class": "combined-theme"
            }
        }
        
        config = theme_config.get(site_type, theme_config["tech"])
        
        # Fill template using string replacement to avoid curly brace issues
        html_content = template
        html_content = html_content.replace('{title}', f"{site_type.title()} News - {datetime.now().strftime('%Y-%m-%d %H:%M')}")
        html_content = html_content.replace('{header_title}', config["header_title"])
        html_content = html_content.replace('{subtitle}', config["subtitle"])
        html_content = html_content.replace('{total_articles}', str(total_articles))
        html_content = html_content.replace('{total_sources}', str(len(articles_dict)))
        html_content = html_content.replace('{generation_time}', datetime.now().strftime('%H:%M'))
        html_content = html_content.replace('{content}', content)
        html_content = html_content.replace('{timestamp}', datetime.now().strftime('%A, %B %d, %Y at %H:%M:%S'))
        html_content = html_content.replace('{theme_class}', config["theme_class"])
        
        with open(filename, 'w', encoding='utf-8') as f:
            f.write(html_content)
        
        logger.info(f"HTML report saved to {filename}")
        return filename


def scrape_tech_news(max_articles_per_site: int = 5):
    """Scrape general tech news"""
    scraper = TechNewsScraper(delay=2.0)
    
    print("Starting tech news scraping...")
    all_articles = scraper.scrape_all_sites(max_articles_per_site, site_type="tech")
    
    total_articles = sum(len(articles) for articles in all_articles.values())
    
    if total_articles > 0:
        # Save results in all formats
        json_file = scraper.save_to_json(all_articles, file_prefix="tech_news")
        csv_file = scraper.save_to_csv(all_articles, file_prefix="tech_news")
        html_file = scraper.save_to_html(all_articles, file_prefix="tech_news", site_type="tech")
        
        print(f"\nTech News - Files saved:")
        print(f"- {json_file}")
        print(f"- {csv_file}")
        print(f"- {html_file}")
    
    return all_articles

def scrape_security_news(max_articles_per_site: int = 5):
    """Scrape cybersecurity news"""
    scraper = TechNewsScraper(delay=2.0)
    
    print("Starting cybersecurity news scraping...")
    all_articles = scraper.scrape_all_sites(max_articles_per_site, site_type="security")
    
    total_articles = sum(len(articles) for articles in all_articles.values())
    
    if total_articles > 0:
        # Save results in all formats with security prefix
        json_file = scraper.save_to_json(all_articles, file_prefix="security_news")
        csv_file = scraper.save_to_csv(all_articles, file_prefix="security_news")
        html_file = scraper.save_to_html(all_articles, file_prefix="security_news", 
                                       site_type="security")
        
        print(f"\nCybersecurity News - Files saved:")
        print(f"- {json_file}")
        print(f"- {csv_file}")
        print(f"- {html_file}")
    
    return all_articles

def scrape_robotics_news(max_articles_per_site: int = 5):
    """Scrape robotics news"""
    scraper = TechNewsScraper(delay=2.0)
    
    print("Starting robotics news scraping...")
    all_articles = scraper.scrape_all_sites(max_articles_per_site, site_type="robotics")
    
    total_articles = sum(len(articles) for articles in all_articles.values())
    
    if total_articles > 0:
        # Save results in all formats with robotics prefix
        json_file = scraper.save_to_json(all_articles, file_prefix="robotics_news")
        csv_file = scraper.save_to_csv(all_articles, file_prefix="robotics_news")
        html_file = scraper.save_to_html(all_articles, file_prefix="robotics_news", 
                                       site_type="robotics")
        
        print(f"\nRobotics News - Files saved:")
        print(f"- {json_file}")
        print(f"- {csv_file}")
        print(f"- {html_file}")
    
    return all_articles

def scrape_linux_news(max_articles_per_site: int = 5):
    """Scrape Linux news"""
    scraper = TechNewsScraper(delay=2.0)
    
    print("Starting Linux news scraping...")
    all_articles = scraper.scrape_all_sites(max_articles_per_site, site_type="linux")
    
    total_articles = sum(len(articles) for articles in all_articles.values())
    
    if total_articles > 0:
        # Save results in all formats with Linux prefix
        json_file = scraper.save_to_json(all_articles, file_prefix="linux_news")
        csv_file = scraper.save_to_csv(all_articles, file_prefix="linux_news")
        html_file = scraper.save_to_html(all_articles, file_prefix="linux_news", 
                                       site_type="linux")
        
        print(f"\nLinux News - Files saved:")
        print(f"- {json_file}")
        print(f"- {csv_file}")
        print(f"- {html_file}")
    
    return all_articles

def main():
    """
    Main function to run the scraper
    """
    print("Enhanced Tech News Scraper v3.0")
    print("Choose what to scrape:")
    print("1. Tech news only")
    print("2. Cybersecurity news only")
    print("3. Robotics news only")
    print("4. Linux news only")
    print("5. All categories (tech + security + robotics + linux)")
    print("6. Custom selection")
    
    choice = input("\nEnter choice (1/2/3/4/5/6): ").strip()
    
    if choice == "1":
        tech_articles = scrape_tech_news()
        total = sum(len(articles) for articles in tech_articles.values())
        print(f"\n✅ Tech news scraping completed! Total articles: {total}")
    
    elif choice == "2":
        security_articles = scrape_security_news()
        total = sum(len(articles) for articles in security_articles.values())
        print(f"\n✅ Cybersecurity news scraping completed! Total articles: {total}")
    
    elif choice == "3":
        robotics_articles = scrape_robotics_news()
        total = sum(len(articles) for articles in robotics_articles.values())
        print(f"\n✅ Robotics news scraping completed! Total articles: {total}")
    
    elif choice == "4":
        linux_articles = scrape_linux_news()
        total = sum(len(articles) for articles in linux_articles.values())
        print(f"\n✅ Linux news scraping completed! Total articles: {total}")
    
    elif choice == "5":
        print("\nScraping all news categories...")
        tech_articles = scrape_tech_news()
        security_articles = scrape_security_news()
        robotics_articles = scrape_robotics_news()
        linux_articles = scrape_linux_news()
        
        tech_total = sum(len(articles) for articles in tech_articles.values())
        security_total = sum(len(articles) for articles in security_articles.values())
        robotics_total = sum(len(articles) for articles in robotics_articles.values())
        linux_total = sum(len(articles) for articles in linux_articles.values())
        
        # Create combined report
        combined_file = create_combined_report(tech_articles, security_articles, 
                                             robotics_articles, linux_articles)
        
        print(f"\n✅ All scraping completed!")
        print(f"Tech articles: {tech_total}")
        print(f"Security articles: {security_total}")
        print(f"Robotics articles: {robotics_total}")
        print(f"Linux articles: {linux_total}")
        print(f"Total articles: {tech_total + security_total + robotics_total + linux_total}")
        print(f"Combined report: {combined_file}")
    
    elif choice == "6":
        print("\nCustom selection:")
        print("Select categories to scrape (separate with commas):")
        print("t = tech, s = security, r = robotics, l = linux")
        categories = input("Enter selection (e.g., t,s,r): ").strip().lower().split(',')
        
        all_results = {}
        total_count = 0
        
        for cat in categories:
            cat = cat.strip()
            if cat == 't':
                print("\nScraping tech news...")
                articles = scrape_tech_news()
                all_results.update({f"tech_{k}": v for k, v in articles.items()})
                total_count += sum(len(articles) for articles in articles.values())
            elif cat == 's':
                print("\nScraping security news...")
                articles = scrape_security_news()
                all_results.update({f"security_{k}": v for k, v in articles.items()})
                total_count += sum(len(articles) for articles in articles.values())
            elif cat == 'r':
                print("\nScraping robotics news...")
                articles = scrape_robotics_news()
                all_results.update({f"robotics_{k}": v for k, v in articles.items()})
                total_count += sum(len(articles) for articles in articles.values())
            elif cat == 'l':
                print("\nScraping Linux news...")
                articles = scrape_linux_news()
                all_results.update({f"linux_{k}": v for k, v in articles.items()})
                total_count += sum(len(articles) for articles in articles.values())
        
        if all_results:
            # Create custom combined report
            scraper = TechNewsScraper()
            custom_file = scraper.save_to_html(
                all_results,
                filename=f"custom_news_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.html",
                file_prefix="custom_news",
                site_type="combined"
            )
            print(f"\n✅ Custom scraping completed! Total articles: {total_count}")
            print(f"Custom report: {custom_file}")
        else:
            print("No valid categories selected.")
    
    else:
        print("Invalid choice. Please run again and select 1-6.")

def create_combined_report(tech_articles: Dict[str, List[Article]], 
                          security_articles: Dict[str, List[Article]],
                          robotics_articles: Dict[str, List[Article]],
                          linux_articles: Dict[str, List[Article]]):
    """
    Create a combined HTML report with all news categories
    """
    scraper = TechNewsScraper()
    
    # Combine all articles
    combined_articles = {}
    
    # Add articles with prefixes
    for site, articles in tech_articles.items():
        combined_articles[f"tech_{site}"] = articles
    
    for site, articles in security_articles.items():
        combined_articles[f"security_{site}"] = articles
    
    for site, articles in robotics_articles.items():
        combined_articles[f"robotics_{site}"] = articles
    
    for site, articles in linux_articles.items():
        combined_articles[f"linux_{site}"] = articles
    
    # Save combined report
    html_file = scraper.save_to_html(
        combined_articles, 
        filename=f"combined_news_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.html",
        file_prefix="combined_news",
        site_type="combined"
    )
    
    return html_file

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
    # debug_single_site('https://www.therobotreport.com')
    # debug_single_site('https://lwn.net')
    # debug_single_site('https://robohub.org')
