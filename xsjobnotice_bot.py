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

KEYWORDS = [
    "নিয়োগ", "চাকরি", "পরীক্ষা", "সময়সূচী", "প্রবেশপত্র", "বিজ্ঞপ্তি",
    "ফলাফল", "মেধা তালিকা", "ভর্তি", "আসন বিন্যাস", "প্রার্থী", "ই-টেন্ডার", "টেন্ডার",
    "circular", "recruitment", "job", "admit card", "exam", "result", "merit list", "admission", "tender"
]

IGNORE_WORDS = ["দেখুন", "ডাউনলোড", "download", "view", "details", "বিস্তারিত", "click here", "pdf"]

DEPT_NAME_MAP = {
    "mopa.gov.bd": "জনপ্রশাসন মন্ত্রণালয়",
    "lgd.gov.bd": "স্থানীয় সরকার বিভাগ",
    "mof.gov.bd": "অর্থ বিভাগ",
    "cabinet.gov.bd": "মন্ত্রিপরিপরিষদ বিভাগ",
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
    if not date_str:
        return None
    clean = date_str.translate(EN_TO_BN_NUM)
    for en_m, bn_m in MONTH_MAP.items():
        clean = re.sub(rf'\b{en_m}\b', bn_m, clean, flags=re.IGNORECASE)
    return clean.strip()

def get_current_bd_datetime():
    bd_dt = datetime.now(timezone.utc) + timedelta(hours=6)
    day = bd_dt.strftime("%d").translate(EN_TO_BN_NUM)
    month = bd_dt.strftime("%m").translate(EN_TO_BN_NUM)
    year = bd_dt.strftime("%Y").translate(EN_TO_BN_NUM)
    time_str = bd_dt.strftime("%I,%M").translate(EN_TO_BN_NUM)
    ampm = "সকাল" if bd_dt.hour < 12 else "বিকাল" if bd_dt.hour < 17 else "সন্ধ্যা" if bd_dt.hour < 20 else "রাত"
    return f"তারিখ: {day}/{month}/{year}, সময়: {ampm} {time_str}টা"

def detect_category(text):
    text_lower = text.lower()
    tags = []
    if any(k in text_lower for k in ["নিয়োগ", "চাকরি", "circular", "recruitment", "job"]):
        tags.append("নিয়োগ")
    if any(k in text_lower for k in ["ফলাফল", "result", "merit list"]):
        tags.append("ফলাফল")
    if any(k in text_lower for k in ["পরীক্ষা", "সময়সূচী", "প্রবেশপত্র", "admit card", "exam"]):
        tags.append("পরীক্ষা")
    if any(k in text_lower for k in ["ভর্তি", "admission"]):
        tags.append("ভর্তি")
    if any(k in text_lower for k in ["টেন্ডার", "tender"]):
        tags.append("টেন্ডার")
    
    return ", ".join(tags) if tags else "নোটিশ"

def load_sent_notices():
    if os.path.exists(SENT_NOTICES_FILE):
        with open(SENT_NOTICES_FILE, "r", encoding="utf-8") as f:
            return set(line.strip() for line in f if line.strip())
    return set()

def save_sent_notice(notice_id):
    with open(SENT_NOTICES_FILE, "a", encoding="utf-8") as f:
        f.write(f"{notice_id}\n")

def extract_site_name(soup, url):
    """ওয়েবসাইটের Title বা Meta Tag থেকে স্বয়ংক্রিয়ভাবে বাংলা দপ্তরের নাম বের করার ফাংশন"""
    domain = urlparse(url).netloc.replace("www.", "").lower()
    
    # ১. ডিকশনারিতে ম্যানুয়ালি সেট করা থাকলে সেটি আগে নিবে
    if domain in DEPT_NAME_MAP:
        return DEPT_NAME_MAP[domain]
    
    # ২. ওয়েবসাইটের <title> ট্যাগ থেকে নাম এক্সট্রাক্ট করা
    try:
        title_tag = soup.find('title')
        if title_tag:
            full_title = title_tag.get_text(strip=True)
            # জাতীয় তথ্য বাতায়ন বা সরকারি ওয়েবসাইটের টাইটেল ফিল্টার করা
            parts = re.split(r'\||-|–', full_title)
            for part in parts:
                clean_part = part.strip()
                # বাংলা অক্ষর আছে এমন অংশ খুঁজে নেওয়া
                if re.search(r'[\u0980-\u09FF]', clean_part) and "Home" not in clean_part and "Welcome" not in clean_part:
                    return clean_part
    except Exception:
        pass

    # ৩. কোনো বাংলা না পাওয়া গেলে ব্যাকআপ হিসেবে ডোমেইন নাম ফেরত দেওয়া
    return domain.upper()

def send_telegram_msg(title, pdf_url, site_name, display_time, category_tag):
    clean_title = html.escape(title.strip())
    clean_site_name = html.escape(site_name.strip())
    
    message = (
        f"📝 <b>শিরোনাম:</b> {clean_title}\n"
        f"⏱️ {display_time}\n"
        f"🏷 <b>ক্যাটাগরি:</b> {category_tag}\n"
        f"🏛 <b>দপ্তর:</b> {clean_site_name}\n"
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
            return

        soup = BeautifulSoup(response.text, 'html.parser')
        
        # স্বয়ংক্রিয়ভাবে বাংলা দপ্তরের নাম নিয়ে আসবে
        site_name = extract_site_name(soup, url)
        
        rows = soup.find_all('tr')
        
        for row in rows:
            anchors = row.find_all('a', href=True)
            if not anchors:
                continue

            title = ""
            file_link = ""
            found_date = ""

            tds = row.find_all('td')
            for td in tds:
                txt = td.get_text(strip=True)
                if re.search(r'(\d{1,4}[-/\.\s]\d{1,2}[-/\.\s]\d{2,4})|(\d{1,2}\s+[A-Za-z\u0980-\u09FF]+\s+\d{4})', txt):
                    found_date = txt
                    break

            for a in anchors:
                text = a.get_text(strip=True)
                href = a['href'].strip()

                if len(text) > 5 and not any(w in text.lower() for w in IGNORE_WORDS) and not title:
                    title = text
                
                if any(ext in href.lower() for ext in ['.pdf', 'download', 'site/view/notices', 'node', 'pages/notices']):
                    file_link = href

            if not title:
                for td in tds:
                    txt = td.get_text(strip=True)
                    if len(txt) > 10 and not any(w in txt.lower() for w in IGNORE_WORDS) and txt != found_date:
                        title = txt
                        break

            if title and file_link:
                is_relevant = any(kw.lower() in title.lower() for kw in KEYWORDS)
                
                if is_relevant:
                    full_pdf_url = urljoin(url, file_link)
                    notice_id = re.sub(r'[^a-zA-Z0-9]', '', full_pdf_url)

                    if notice_id not in sent_notices:
                        date_str = format_to_bangla_date(found_date)
                        display_time = f"তারিখ: {date_str}" if date_str else get_current_bd_datetime()
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
