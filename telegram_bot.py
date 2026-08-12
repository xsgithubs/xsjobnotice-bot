import os
import requests
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

def load_history():
    if os.path.exists(HISTORY_FILE):
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            return set(line.strip() for line in f if line.strip())
    return set()

def save_history(url):
    with open(HISTORY_FILE, "a", encoding="utf-8") as f:
        f.write(url + "\n")

def send_telegram_pdf(file_path, caption):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendDocument"
    with open(file_path, "rb") as doc:
        payload = {"chat_id": CHAT_ID, "caption": caption}
        files = {"document": doc}
        requests.post(url, data=payload, files=files)

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

    headers = {'User-Agent': 'Mozilla/5.0'}

    for site_url in urls:
        try:
            res = requests.get(site_url, headers=headers, timeout=15, verify=False)
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
                            pdf_res = requests.get(pdf_url, headers=headers, timeout=20, verify=False)
                            if pdf_res.status_code == 200 and len(pdf_res.content) > 10240:
                                server_dt = get_server_timestamp(pdf_res)
                                time_str = server_dt.strftime("%d-%m-%Y %I:%M %p")
                                
                                temp_file = "temp_notice.pdf"
                                with open(temp_file, "wb") as f:
                                    f.write(pdf_res.content)

                                caption = f"📌 {title[:100]}\n🕒 আপলোড সময়: {time_str}"
                                send_telegram_pdf(temp_file, caption)

                                save_history(pdf_url)
                                processed_urls.add(pdf_url)
                                
                                if os.path.exists(temp_file):
                                    os.remove(temp_file)
        except Exception:
            pass

if __name__ == "__main__":
    check_and_notify()
