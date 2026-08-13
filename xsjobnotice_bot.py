import os
import re
import html
import logging
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
from datetime import datetime, timezone, timedelta

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
SENT_NOTICES_FILE = "downloaded_history.txt"
URLS_FILE = "xsjobnoticeurls.txt"

# ওয়েবসাইট অনুযায়ী দপ্তরের বাংলা নামের তালিকা
DEPT_NAME_MAP = {
    "mopa.gov.bd": "জনপ্রশাসন মন্ত্রণালয়",
    "lgd.gov.bd": "স্থানীয় সরকার বিভাগ",
    "mof.gov.bd": "অর্থ বিভাগ",
    "cabinet.gov.bd": "মন্ত্রিপরিষদ বিভাগ",
    "barisaldiv.gov.bd": "বিভাগীয় কমিশনারের কার্যালয়, বরিশাল",
}

EN_TO_BN_NUM = str.maketrans("0123456789", "০১২৩৪৫৬৭৮৯")

MONTH_MAP = {
    "Jan": "জানুয়ারি", "Feb": "ফেব্রুয়ারি", "Mar": "মার্চ", "Apr": "এপ্রিল",
    "May": "মে", "Jun": "জুন", "Jul": "জুলাই", "Aug": "আগস্ট",
    "Sep": "সেপ্টেম্বর", "Oct": "অক্টোবর", "Nov": "নভেম্বর", "Dec": "ডিসেম্বর",
    "January": "জানুয়ারি", "February": "ফেব্রুয়ারি", "March": "মার্চ",
    "April": "এপ্রিল", "June": "জুন", "July": "জুলাই", "August": "আগস্ট",
    "September": "সেপ্টেম্বর", "October": "অক্টোবর", "November": "নভেম্বর", "December": "ডিসেম্বর"
}

def format_to_bangla_date(date_str):
    """টেবিল থেকে পাওয়া তারিখ ফরম্যাট করা"""
    if not date_str:
        return None
    
    clean = date_str.translate(EN_TO_BN_NUM)
    for en_m, bn_m in MONTH_MAP.items():
        clean = re.sub(rf'\b{en_m}\b', bn_m, clean, flags=re.IGNORECASE)
    
    return clean.strip()

def get_current_bd_datetime():
    """ব্যাকআপ তারিখ ও সময় (বাংলাদেশ সময়)"""
    bd_dt = datetime.now(timezone.utc) + timedelta(hours=6)
    
    day = bd_dt.strftime("%d").translate(EN_TO_BN_NUM)
    month_en = bd_dt.strftime("%B")
    month = MONTH_MAP.get(month_en, month_en)
    year = bd_dt.strftime("%Y").translate(EN_TO_BN_NUM)
    
    time_str = bd_dt.strftime("%I.%M").translate(EN_TO_BN_NUM)
    ampm = "সকাল" if bd_dt.hour < 12 else "বিকাল" if bd_dt.hour < 17 else "সন্ধ্যা" if bd_dt.hour < 20 else "রাত"
    
    return f"{day} {month} {year}; {ampm} {time_str} টা"

def detect_category(text):
    """শিরোনাম দেখে ক্যাটাগরি নির্ধারণ করার ফাংশন"""
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

def load_sent_notices():
    if os.path.exists(SENT_NOTICES_FILE):
        with open(SENT_NOTICES_FILE, "r", encoding="utf-8") as f:
            return set(line.strip() for line in f if line.strip())
    return set()

def save_sent_notice(notice_id):
    with open(SENT_NOTICES_FILE, "a", encoding="utf-8") as f:
        f.write(f"{notice_id}\n")

def get_site_name(url):
    domain = urlparse(url).netloc.replace("www.", "").lower()
    return DEPT_NAME_MAP.get(domain, domain.upper())

def send_telegram_msg(title, pdf_url, site_name, display_time, category_tag):
    clean_title = html.escape(title.strip())
    clean_site_name = html.escape(site_name.strip())
    
    message = (
        f"⏱️ <b>তারিখ/সময়:</b> {display_time}\n"
        f"🏷 <b>ক্যাটাগরি:</b> {category_tag}\n"
        f"🏛 <b>দপ্তর:</b> {clean_site_name}\n\n"
        f"📝 <b>শিরোনাম:</b>\n<b>{clean_title}</b>\n\n"
        f"🔗 <a href='{pdf_url}'>ডাউনলোড / বিস্তারিত দেখুন</a>"
    )
    
    telegram_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }
    
    try:
        res = requests.post(telegram_url, json=payload, timeout=15)
        res.raise_for_status()
        logging.info(f"Sent: {title}")
        return True
    except Exception as e:
        logging.error(f"Telegram error: {e}")
        return False

def scrape_site(url, sent_notices):
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    }
    
    try:
        response = requests.get(url, headers=headers, timeout=20, verify=False)
        if response.status_code != 200:
            logging.warning(f"Could not fetch {url}, Code: {response.status_code}")
            return

        soup = BeautifulSoup(response.text, 'html.parser')
        site_name = get_site_name(url)
        
        rows = soup.find_all('tr')
        
        for row in rows:
            anchors = row.find_all('a', href=True)
            if not anchors:
                continue

            title = ""
            file_link = ""
            found_date = ""

            # ১. নোটিশ বোর্ডের টেবিল ঘর থেকে প্রকাশের তারিখ খোঁজা
            tds = row.find_all('td')
            for td in tds:
                txt = td.get_text(strip=True)
                if re.search(r'(\d{1,4}[-/\.\s]\d{1,2}[-/\.\s]\d{2,4})|(\d{1,2}\s+[A-Za-z\u0980-\u09FF]+\s+\d{4})', txt):
                    found_date = txt
                    break

            for a in anchors:
                text = a.get_text(strip=True)
                href = a['href'].strip()

                if len(text) > 3 and text not in ["দেখুন", "ডাউনলোড", "Download", "View"] and not title:
                    title = text
                
                if any(ext in href.lower() for ext in ['.pdf', 'download', 'site/view/notices', 'node', 'pages/notices']):
                    file_link = href

            if not title or title in ["দেখুন", "ডাউনলোড", "Download", "View"]:
                for td in tds:
                    txt = td.get_text(strip=True)
                    if len(txt) > 5 and txt not in ["দেখুন", "ডাউনলোড", "Download", "View"] and txt != found_date:
                        title = txt
                        break

            if title and file_link:
                full_pdf_url = urljoin(url, file_link)
                notice_id = re.sub(r'[^a-zA-Z0-9]', '', full_pdf_url)

                if notice_id not in sent_notices:
                    display_time = format_to_bangla_date(found_date) or get_current_bd_datetime()
                    category_tag = detect_category(title)
                    
                    if send_telegram_msg(title, full_pdf_url, site_name, display_time, category_tag):
                        sent_notices.add(notice_id)
                        save_sent_notice(notice_id)

    except Exception as e:
        logging.error(f"Error scraping {url}: {e}")

def main():
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logging.error("Missing BOT TOKEN or CHAT ID")
        return

    sent_notices = load_sent_notices()
    
    if os.path.exists(URLS_FILE):
        with open(URLS_FILE, "r", encoding="utf-8") as f:
            urls = [line.strip() for line in f if line.strip()]
        
        for url in urls:
            logging.info(f"Scraping: {url}")
            scrape_site(url, sent_notices)
    else:
        logging.error(f"{URLS_FILE} file missing!")

if __name__ == "__main__":
    requests.packages.urllib3.disable_warnings()
    main()
