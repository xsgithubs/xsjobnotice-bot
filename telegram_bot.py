import os
import time
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin
from datetime import datetime
from email.utils import parsedate_to_datetime

# টেলিগ্রাম কনফিগারেশন
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
CHAT_ID = os.environ.get("CHAT_ID")

KEYWORDS = [
    "নিয়োগ", "চাকরি", "পরীক্ষা", "সময়সূচী", "প্রবেশপত্র", "বিজ্ঞপ্তি",
    "ফলাফল", "মেধা তালিকা", "ভর্তি", "আসন বিন্যাস",
    "circular", "recruitment", "job", "admit card", "exam", "result", "merit list", "admission"
]

HISTORY_FILE = "downloaded_history.txt"

def load_history():
    if os.path.exists(HISTORY_FILE):
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            return set(line.strip() for line in f if line.strip())
    return set()

def save_history(url):
    with open(HISTORY_FILE, "a", encoding="utf-8") as f:
        f.write(url + "\n")

def detect_category(text):
    """নোটিশের টাইটেল দেখে হ্যাশট্যাগ ক্যাটাগরি তৈরি করে"""
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

def send_telegram_pdf(file_path, caption):
    """HTML ফরম্যাটিং ব্যবহার করে টেলিগ্রামে মেসেজ ও PDF পাঠায়"""
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendDocument"
    with open(file_path, "rb") as doc:
        payload = {
            "chat_id": CHAT_ID, 
            "caption": caption,
            "parse_mode": "HTML"
        }
        files = {"document": doc}
        requests.post(url, data=payload, files=files, timeout=30)

def get_server_timestamp(response):
    server_date_str = response.headers.get('Last-Modified') or response.headers.get('Date')
    if server_date_str:
        try:
            return parsedate_to_datetime(server_date_str)
        except Exception:
            pass
    return datetime.now()

def check_and_notify():
    processed_urls = load_history()
    
    if not os.path.exists("urls.txt"):
        return
        
    with open("urls.txt", "r", encoding="utf-8") as f:
        urls = [line.strip() for line in f if line.strip() and not line.startswith("#")]

    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}

    for site_url in urls:
        try:
            # ৩ বার চেষ্টা করার রিট্রাই লজিক
            res = None
            for _ in range(3):
                try:
                    res = requests.get(site_url, headers=headers, timeout=15, verify=False)
                    if res.status_code == 200:
                        break
                except Exception:
                    time.sleep(2)
            
            if not res or res.status_code != 200:
                continue

            soup = BeautifulSoup(res.text, 'html.parser')
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
                                sub_res = requests.get(target_url, headers=headers, timeout=10, verify=False)
                                sub_soup = BeautifulSoup(sub_res.text, 'html.parser')
                                pdf_a = sub_soup.find('a', href=lambda h: h and '.pdf' in h.lower())
                                if pdf_a:
                                    pdf_url = urljoin(target_url, pdf_a['href'])
                                else:
                                    continue
                            except Exception:
                                continue

                        if pdf_url not in processed_urls:
                            pdf_res = requests.get(pdf_url, headers=headers, timeout=25, verify=False)
                            
                            # Content-Type ও সাইজ ভ্যালিডেশন
                            content_type = pdf_res.headers.get('Content-Type', '').lower()
                            if pdf_res.status_code == 200 and len(pdf_res.content) > 10240 and ('pdf' in content_type or pdf_url.endswith('.pdf')):
                                server_dt = get_server_timestamp(pdf_res)
                                time_str = server_dt.strftime("%d-%m-%Y %I:%M %p")
                                category_tag = detect_category(title)
                                
                                temp_file = "temp_notice.pdf"
                                with open(temp_file, "wb") as f:
                                    f.write(pdf_res.content)

                                # সুন্দর HTML ক্যাপশন
                                caption = (
                                    f"📌 <b>{title[:120]}</b>\n\n"
                                    f"🏷 <b>ক্যাটাগরি:</b> {category_tag}\n"
                                    f"🕒 <b>প্রকাশের সময়:</b> {time_str}\n"
                                    f"🔗 <a href='{site_url}'>উৎস ওয়েবসাইট</a>"
                                )
                                
                                send_telegram_pdf(temp_file, caption)

                                save_history(pdf_url)
                                processed_urls.add(pdf_url)
                                
                                if os.path.exists(temp_file):
                                    os.remove(temp_file)
        except Exception:
            pass

if __name__ == "__main__":
    check_and_notify()
