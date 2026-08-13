import os
import asyncio
import aiohttp
from bs4 import BeautifulSoup
from urllib.parse import urljoin
from datetime import datetime
from email.utils import parsedate_to_datetime

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
CHAT_ID = os.environ.get("CHAT_ID")

KEYWORDS = [
    "নিয়োগ", "চাকরি", "পরীক্ষা", "সময়সূচী", "প্রবেশপত্র", "বিজ্ঞপ্তি",
    "ফলাফল", "মেধা তালিকা", "ভর্তি", "আসন বিন্যাস",
    "circular", "recruitment", "job", "admit card", "exam", "result", "merit list", "admission"
]

HISTORY_FILE = "downloaded_history.txt"
URLS_FILE = "xsjobnoticeurls.txt"
CONCURRENCY_LIMIT = 20 

def load_history():
    if os.path.exists(HISTORY_FILE):
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            return set(line.strip() for line in f if line.strip())
    return set()

def save_history(url):
    with open(HISTORY_FILE, "a", encoding="utf-8") as f:
        f.write(url + "\n")

def detect_category(text):
    text_lower = text.lower()
    tags = []
    if any(k in text_lower for k in ["নিয়োগ", "চাকরি", "circular", "recruitment", "job"]):
        tags.append("#নিয়োগ")
    if any(k in text_lower for k in ["ফলাফল", "result", "merit list"]):
        tags.append("#ফলাফল")
    if any(k in text_lower for k in ["পরীক্ষা", "সময়সূচী", "প্রবেশপত্র", "admit card", "exam"]):
        tags.append("#পরীক্ষা")
    if any(k in text_lower for k in ["ভর্তি", "admission"]):
        tags.append("#ভর্তি")
    
    return " ".join(tags) if tags else "#নোটিশ"

async def send_telegram_pdf(session, file_path, caption):
    if not TELEGRAM_TOKEN or not CHAT_ID:
        print("Telegram Token or Chat ID missing!")
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendDocument"
    data = aiohttp.FormData()
    data.add_field('chat_id', CHAT_ID)
    data.add_field('caption', caption)
    data.add_field('parse_mode', 'HTML')
    data.add_field('document', open(file_path, 'rb'), filename="notice.pdf", content_type='application/pdf')
    
    try:
        async with session.post(url, data=data, timeout=30) as resp:
            await resp.json()
    except Exception as e:
        print(f"Telegram upload error: {e}")

def get_server_timestamp(headers):
    server_date_str = headers.get('Last-Modified') or headers.get('Date')
    if server_date_str:
        try:
            return parsedate_to_datetime(server_date_str)
        except Exception:
            pass
    return datetime.now()

async def fetch_site(semaphore, session, site_url, processed_urls, history_lock):
    async with semaphore:
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
        try:
            async with session.get(site_url, headers=headers, timeout=15, ssl=False) as res:
                if res.status != 200:
                    return
                html_text = await res.text()

            soup = BeautifulSoup(html_text, 'html.parser')
            rows = soup.find_all('tr')

            for row in rows:
                row_text = row.get_text()
                if any(kw in row_text.lower() for kw in KEYWORDS):
                    tds = row.find_all('td')
                    title = ""
                    for td in tds:
                        text = td.get_text(strip=True)
                        if text and not text.isdigit() and text != "দেখুন" and len(text) > len(title):
                            title = text
                    
                    links = row.find_all('a', href=True)
                    target_url = None
                    for a in links:
                        href = a['href']
                        if '.pdf' in href.lower() or 'site/view/notices' in href or 'download' in href.lower():
                            target_url = urljoin(site_url, href)
                            break
                    
                    if target_url:
                        pdf_url = target_url
                        if '.pdf' not in target_url.lower():
                            try:
                                async with session.get(target_url, headers=headers, timeout=10, ssl=False) as sub_res:
                                    sub_html = await sub_res.text()
                                sub_soup = BeautifulSoup(sub_html, 'html.parser')
                                pdf_a = sub_soup.find('a', href=lambda h: h and '.pdf' in h.lower())
                                if pdf_a:
                                    pdf_url = urljoin(target_url, pdf_a['href'])
                                else:
                                    continue
                            except Exception:
                                continue

                        if pdf_url not in processed_urls:
                            async with session.get(pdf_url, headers=headers, timeout=25, ssl=False) as pdf_res:
                                content_type = pdf_res.headers.get('Content-Type', '').lower()
                                content = await pdf_res.read()
                                
                                if pdf_res.status == 200 and len(content) > 10240 and ('pdf' in content_type or pdf_url.endswith('.pdf')):
                                    server_dt = get_server_timestamp(pdf_res.headers)
                                    time_str = server_dt.strftime("%d-%m-%Y %I:%M %p")
                                    category_tag = detect_category(title)
                                    
                                    safe_filename = f"temp_{abs(hash(pdf_url))}.pdf"
                                    with open(safe_filename, "wb") as f:
                                        f.write(content)

                                    caption = (
                                        f"📌 <b>{title[:120]}</b>\n\n"
                                        f"🏷 <b>ক্যাটাগরি:</b> {category_tag}\n"
                                        f"🕒 <b>প্রকাশের সময়:</b> {time_str}\n"
                                        f"🔗 <a href='{site_url}'>উৎস ওয়েবসাইট</a>"
                                    )
                                    
                                    await send_telegram_pdf(session, safe_filename, caption)

                                    async with history_lock:
                                        save_history(pdf_url)
                                        processed_urls.add(pdf_url)
                                    
                                    if os.path.exists(safe_filename):
                                        os.remove(safe_filename)
        except Exception:
            pass

async def main():
    processed_urls = load_history()
    
    if not os.path.exists(URLS_FILE):
        print(f"{URLS_FILE} file not found!")
        return
        
    with open(URLS_FILE, "r", encoding="utf-8") as f:
        urls = list(set([line.strip() for line in f if line.strip().startswith("http")]))

    if not urls:
        print(f"No valid URLs found in {URLS_FILE}")
        return

    semaphore = asyncio.Semaphore(CONCURRENCY_LIMIT)
    history_lock = asyncio.Lock()

    conn = aiohttp.TCPConnector(limit=100, ssl=False)
    async with aiohttp.ClientSession(connector=conn) as session:
        tasks = [fetch_site(semaphore, session, url, processed_urls, history_lock) for url in urls]
        await asyncio.gather(*tasks)

if __name__ == "__main__":
    asyncio.run(main())
