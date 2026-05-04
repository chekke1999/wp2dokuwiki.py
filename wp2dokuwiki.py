import os
import re
import html
import json
import uuid
import hashlib
import configparser
import requests
from urllib.parse import urlparse, unquote
from bs4 import BeautifulSoup
from datetime import datetime, timezone, timedelta

# ==========================================
# 1. 設定の読み込みと準備
# ==========================================
script_dir = os.path.dirname(os.path.abspath(__file__))
config = configparser.ConfigParser()
config.read(os.path.join(script_dir, 'config.ini'), encoding='utf-8')

WP_URL = config['WordPress']['url'].rstrip('/')
WP_USER = config['WordPress']['username']
WP_PASS = config['WordPress']['app_password']
DATA_DIR = config['DokuWiki']['data_dir']

TZ_OFFSET = int(config.get('Settings', 'timezone_offset', fallback=9))
TZ = timezone(timedelta(hours=TZ_OFFSET))
INCLUDE_FQDN = config.getboolean('Settings', 'include_fqdn_in_redirects', fallback=False)
USE_ORIGINAL = config.getboolean('Settings', 'use_original_image', fallback=True)
USE_IMAGEBOX = config.getboolean('Settings', 'use_imagebox_plugin', fallback=False)

API_BASE = f"{WP_URL}/wp-json/wp/v2"
AUTH = (WP_USER, WP_PASS)

PAGES_BASE = os.path.join(DATA_DIR, 'pages', 'blog')
MEDIA_BASE = os.path.join(DATA_DIR, 'media', 'blog')

def ensure_dir(path):
    if not os.path.exists(path):
        os.makedirs(path)

ensure_dir(PAGES_BASE)
ensure_dir(MEDIA_BASE)

image_map_data = {"images": {}, "pages": {}}
url_to_uuid_map = {}

# ==========================================
# 2. ユーティリティ関数
# ==========================================
def get_file_md5(filepath):
    hash_md5 = hashlib.md5()
    try:
        with open(filepath, "rb") as f:
            for chunk in iter(lambda: f.read(4096), b""):
                hash_md5.update(chunk)
        return hash_md5.hexdigest()
    except:
        return None

def sanitize_filename(title):
    decoded = unquote(title)
    decoded = decoded.lower()
    clean = re.sub(r'[\\/*?:"<>|#]+', '', decoded)
    clean = re.sub(r'[ \s\(\)（）『』「」【】。、，,！!？?\'\'""’‘“”]', '_', clean)
    clean = re.sub(r'_+', '_', clean)
    return clean.strip('_')

def get_original_image_url(url):
    return re.sub(r'((?:-\d+x\d+|-scaled|-e\d+)+)(\.[a-zA-Z]+)$', r'\2', url)

def download_image(img_url, save_dir, unix_time):
    try:
        parsed_url = urlparse(img_url)
        filename = unquote(os.path.basename(parsed_url.path)).lower()
        save_path = os.path.join(save_dir, filename)
        
        if os.path.exists(save_path):
            return filename, save_path

        res = requests.get(img_url, stream=True)
        if res.status_code == 200:
            with open(save_path, 'wb') as f:
                for chunk in res.iter_content(1024):
                    f.write(chunk)
            os.utime(save_path, (unix_time, unix_time))
            return filename, save_path
        else:
            return None, None
    except Exception as e:
        print(f"画像アクセスエラー ({img_url}): {e}")
        return None, None

def process_and_register_image(target_url, media_dir, unix_time, page_id):
    original_url = get_original_image_url(target_url)
    
    parsed_target = urlparse(target_url)
    parsed_original = urlparse(original_url)
    target_filename = unquote(os.path.basename(parsed_target.path)).lower()

    dl_orig_name, dl_orig_path = None, None
    dl_target_name, dl_target_path = None, None

    if target_url != original_url:
        dl_orig_name, dl_orig_path = download_image(original_url, media_dir, unix_time)
        dl_target_name, dl_target_path = download_image(target_url, media_dir, unix_time)
    else:
        dl_target_name, dl_target_path = download_image(target_url, media_dir, unix_time)

    if not dl_orig_name and not dl_target_name:
        return None

    original_path = parsed_original.path
    if original_path in url_to_uuid_map:
        group_uuid = url_to_uuid_map[original_path]
    else:
        group_uuid = str(uuid.uuid4())
        url_to_uuid_map[original_path] = group_uuid
        image_map_data["images"][group_uuid] = {}

    if dl_orig_name and dl_orig_path:
        orig_md5 = get_file_md5(dl_orig_path)
        if orig_md5 and orig_md5 not in image_map_data["images"][group_uuid]:
            image_map_data["images"][group_uuid][orig_md5] = {
                "filename": dl_orig_name,
                "filesize": os.path.getsize(dl_orig_path),
                "is_original": True
            }

    if dl_target_name and dl_target_path:
        target_md5 = get_file_md5(dl_target_path)
        if target_md5 and target_md5 not in image_map_data["images"][group_uuid]:
            is_orig = True if not dl_orig_name else False
            image_map_data["images"][group_uuid][target_md5] = {
                "filename": dl_target_name,
                "filesize": os.path.getsize(dl_target_path),
                "is_original": is_orig
            }

    if page_id not in image_map_data["pages"]:
        image_map_data["pages"][page_id] = {}

    written_filename = None
    if dl_orig_name and USE_ORIGINAL:
        written_filename = dl_orig_name
    elif dl_target_name:
        written_filename = dl_target_name
    elif dl_orig_name:
        written_filename = dl_orig_name

    if group_uuid not in image_map_data["pages"][page_id]:
        image_map_data["pages"][page_id][group_uuid] = {
            "current": written_filename,
            "history": {}
        }
    
    if dl_orig_name:
        image_map_data["pages"][page_id][group_uuid]["history"]["original"] = dl_orig_name
    if dl_target_name and dl_target_name != dl_orig_name:
        image_map_data["pages"][page_id][group_uuid]["history"]["processed"] = dl_target_name

    image_map_data["pages"][page_id][group_uuid]["current"] = written_filename

    return written_filename

# ==========================================
# DokuWiki構文変換処理
# ==========================================
def convert_html_to_dokuwiki(html_content, media_dir, namespace_path, unix_time, page_id):
    html_content = html.unescape(html_content)
    html_content = re.sub(r'<!-- wp:.*?-->', '', html_content, flags=re.DOTALL)
    html_content = re.sub(r'<!-- /wp:.*?-->', '', html_content, flags=re.DOTALL)
    
    soup = BeautifulSoup(html_content, 'html.parser')

    # 【手順1】WordPressギャラリーの親ラッパーを解体し、各画像をフラットにする
    for gallery_figure in soup.find_all('figure', class_=lambda c: c and 'wp-block-gallery' in c):
        gallery_figure.unwrap()
        
    for gallery_ul in soup.find_all('ul', class_=lambda c: c and ('gallery' in c)):
        for li in gallery_ul.find_all('li', recursive=False):
            li.unwrap()
        gallery_ul.unwrap()

    # 【手順2】個別の figure タグを処理 (キャプション付き画像)
    for figure in soup.find_all('figure'):
        img = figure.find('img')
        if not img:
            continue

        img_url = img.get('src')
        if not img_url:
            continue

        # キャプションの取得と二重出力防止のための削除
        caption_text = ""
        figcaption = figure.find('figcaption')
        if figcaption:
            caption_text = figcaption.get_text(strip=True)
            figcaption.decompose()

        target_url = img_url
        parent_a = img.find_parent('a')
        if parent_a:
            href = parent_a.get('href', '')
            if re.search(r'\.(jpe?g|png|gif|webp)(\?.*)?$', href, re.IGNORECASE):
                target_url = href

        write_filename = process_and_register_image(target_url, media_dir, unix_time, page_id)
        if write_filename:
            doku_img = f"{{{{:blog:{namespace_path}:{write_filename}|{caption_text}}}}}"
            
            if caption_text and USE_IMAGEBOX:
                doku_img = f"[{doku_img}]"
            
            # 【変更点】 置換時に末尾に改行 (\n) を追加し、画像同士が横に繋がらないようにする
            figure.replace_with(doku_img + "\n")

    # 【手順3】figureで囲まれていない単独の img タグの処理
    for img in soup.find_all('img'):
        img_url = img.get('src')
        if not img_url: continue

        target_url = img_url
        parent_a = img.parent
        if parent_a and parent_a.name == 'a':
            href = parent_a.get('href', '')
            if re.search(r'\.(jpe?g|png|gif|webp)(\?.*)?$', href, re.IGNORECASE):
                target_url = href
                parent_a.unwrap()

        write_filename = process_and_register_image(target_url, media_dir, unix_time, page_id)
        if write_filename:
            img.replace_with(f"{{{{:blog:{namespace_path}:{write_filename}|}}}}}}")

    # その他のaタグの処理
    for a in soup.find_all('a'):
        href = a.get('href', '')
        if re.search(r'\.(jpe?g|png|gif|webp)(\?.*)?$', href, re.IGNORECASE) and 'wp-content/uploads' in href:
            write_filename = process_and_register_image(href, media_dir, unix_time, page_id)
            if write_filename:
                a.replace_with(f"{{{{:blog:{namespace_path}:{write_filename}|}}}}}}")
                continue
                
        text = a.get_text(strip=True)
        if text:
            a.replace_with(f"[[{href}|{text}]]")
        else:
            a.replace_with(f"[[{href}]]")

    for strong in soup.find_all(['strong', 'b']):
        strong.insert_before('**')
        strong.insert_after('**')
        strong.unwrap()

    for em in soup.find_all(['em', 'i']):
        em.insert_before('//')
        em.insert_after('//')
        em.unwrap()

    for table in soup.find_all('table'):
        doku_table = ""
        for tr in table.find_all('tr'):
            row_str = ""
            is_header = False
            for cell in tr.find_all(['th', 'td']):
                if cell.name == 'th':
                    is_header = True
                    sep = '^'
                else:
                    sep = '|'
                cell_text = cell.get_text().replace('\n', ' ').strip()
                row_str += f"{sep} {cell_text} "
            
            if row_str:
                last_sep = '^' if is_header else '|'
                doku_table += f"{row_str}{last_sep}\n"
                
        table.replace_with(f"\n\n{doku_table}\n\n")

    for h in soup.find_all(['h1', 'h2', 'h3', 'h4', 'h5', 'h6']):
        level = int(h.name[1])
        eq = '=' * (7 - level)
        h.insert_before(f'\n\n{eq} ')
        h.insert_after(f' {eq}\n\n')
        h.unwrap()

    # ギャラリー以外の純粋な箇条書きの処理
    for ul in soup.find_all('ul'):
        for li in ul.find_all('li', recursive=False):
            li.insert_before('  * ')
            li.insert_after('\n')
            li.unwrap()
        ul.insert_before('\n')
        ul.insert_after('\n')
        ul.unwrap()

    for p in soup.find_all('p'):
        p.insert_before('\n\n')
        p.insert_after('\n\n')
        p.unwrap()

    text = soup.get_text()
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()

# ==========================================
# 3. WordPress API取得
# ==========================================
def get_categories():
    print("カテゴリ情報を取得中...")
    categories = {}
    url = f"{API_BASE}/categories?per_page=100"
    while url:
        res = requests.get(url, auth=AUTH)
        res.raise_for_status()
        for cat in res.json():
            raw_name = html.unescape(cat['name'])
            categories[cat['id']] = sanitize_filename(raw_name)
        url = res.links.get('next', {}).get('url')
    return categories

def get_posts():
    print("投稿データを取得中...")
    posts = []
    url = f"{API_BASE}/posts?status=publish,draft,private&per_page=50"
    while url:
        res = requests.get(url, auth=AUTH)
        if res.status_code != 200:
            print(f"投稿の取得に失敗しました: {res.status_code}")
            break
        data = res.json()
        posts.extend(data)
        url = res.links.get('next', {}).get('url')
        print(f"  ... {len(posts)} 件取得済み")
    return posts

# ==========================================
# 4. メイン処理
# ==========================================
def main():
    cat_map = get_categories()
    posts = get_posts()
    redirect_list = []
    
    print(f"\n合計 {len(posts)} 件の投稿を処理します...")
    
    for post in posts:
        date_raw = post['date']
        dt = datetime.fromisoformat(date_raw).replace(tzinfo=TZ)
        unix_time = dt.timestamp()
        date_str = date_raw.replace('T', ' ')
        
        title_raw = html.unescape(unquote(post['title']['rendered']))
        title_clean = sanitize_filename(title_raw)
        
        if not title_clean:
            title_clean = str(post['id'])
            
        status = post['status']
        
        if status == 'draft':
            namespace = "drafts"
        elif status == 'private':
            namespace = "private"
        else:
            cat_ids = post.get('categories', [])
            if cat_ids and cat_ids[0] in cat_map:
                namespace = cat_map[cat_ids[0]]
            else:
                namespace = "uncategorized"

        page_id = f"blog:{namespace}:{title_clean}"

        post_page_dir = os.path.join(PAGES_BASE, namespace)
        post_media_dir = os.path.join(MEDIA_BASE, namespace)
        ensure_dir(post_page_dir)
        ensure_dir(post_media_dir)

        html_content = post['content']['rendered']
        dokuwiki_content = convert_html_to_dokuwiki(html_content, post_media_dir, namespace, unix_time, page_id)

        txt_path = os.path.join(post_page_dir, f"{title_clean}.txt")
        final_content = f"~~META: date created = {date_str} ~~\n\n====== {title_raw} ======\n\n{dokuwiki_content}"

        with open(txt_path, 'w', encoding='utf-8') as f:
            f.write(final_content)
            
        os.utime(txt_path, (unix_time, unix_time))
        print(f"出力完了: [{page_id}] ({status})")

        wp_link = post.get('link', '')
        if not INCLUDE_FQDN:
            wp_link = urlparse(wp_link).path
        redirect_list.append(f"301 {wp_link} {page_id}")

    redirects_path = os.path.join(script_dir, 'redirects.txt')
    with open(redirects_path, 'w', encoding='utf-8') as f:
        f.write("\n".join(redirect_list) + "\n")
    print(f"\nリダイレクト設定ファイルを作成しました: {redirects_path}")

    image_map_path = os.path.join(script_dir, 'image_map.json')
    with open(image_map_path, 'w', encoding='utf-8') as f:
        json.dump(image_map_data, f, ensure_ascii=False, indent=4)
    print(f"画像マッピングリストを作成しました: {image_map_path}")

if __name__ == '__main__':
    main()