import os
import re
import html
import configparser
import requests
from urllib.parse import urlparse, unquote
from bs4 import BeautifulSoup
from datetime import datetime, timezone, timedelta

# ==========================================
# 1. 設定の読み込みと準備
# ==========================================
config = configparser.ConfigParser()
config.read('config.ini', encoding='utf-8')

WP_URL = config['WordPress']['url']
WP_USER = config['WordPress']['username']
WP_PASS = config['WordPress']['app_password']
DATA_DIR = config['DokuWiki']['data_dir']

TZ_OFFSET = int(config.get('Settings', 'timezone_offset', fallback=9))
TZ = timezone(timedelta(hours=TZ_OFFSET))
INCLUDE_FQDN = config.getboolean('Settings', 'include_fqdn_in_redirects', fallback=False)

API_BASE = f"{WP_URL}/wp-json/wp/v2"
AUTH = (WP_USER, WP_PASS)

PAGES_BASE = os.path.join(DATA_DIR, 'pages', 'blog')
MEDIA_BASE = os.path.join(DATA_DIR, 'media', 'blog')

def ensure_dir(path):
    if not os.path.exists(path):
        os.makedirs(path)

ensure_dir(PAGES_BASE)
ensure_dir(MEDIA_BASE)

# ==========================================
# 2. WordPress APIからデータを取得する関数
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
# 3. テキスト・HTML・ファイル操作関数
# ==========================================
def sanitize_filename(title):
    decoded = unquote(title)
    decoded = decoded.lower()
    
    # 完全にOSで使えない記号を消す
    clean = re.sub(r'[\\/*?:"<>|#]+', '', decoded)
    
    # アポストロフィや引用符も含めてアンダーバーに置換 (DokuWikiのURL仕様対策)
    clean = re.sub(r'[ \s\(\)（）『』「」【】。、，,！!？?\'\'""’‘“”]', '_', clean)
    
    # 連続するアンダーバーを1つにまとめる
    clean = re.sub(r'_+', '_', clean)
    
    return clean.strip('_')

def get_original_image_url(url):
    return re.sub(r'-\d+x\d+(\.[a-zA-Z]+)$', r'\1', url)

def download_image(img_url, save_dir, unix_time):
    try:
        parsed_url = urlparse(img_url)
        filename = unquote(os.path.basename(parsed_url.path)).lower()
        save_path = os.path.join(save_dir, filename)
        
        if not os.path.exists(save_path):
            res = requests.get(img_url, stream=True)
            if res.status_code == 200:
                with open(save_path, 'wb') as f:
                    for chunk in res.iter_content(1024):
                        f.write(chunk)
                os.utime(save_path, (unix_time, unix_time))
                return filename
        return filename
    except Exception as e:
        print(f"画像ダウンロード失敗 ({img_url}): {e}")
        return None

def convert_html_to_dokuwiki(html_content, media_dir, namespace_path, unix_time):
    # HTMLエンティティ(&#8217;等)をデコード
    html_content = html.unescape(html_content)
    
    html_content = re.sub(r'<!-- wp:.*?-->', '', html_content, flags=re.DOTALL)
    html_content = re.sub(r'<!-- /wp:.*?-->', '', html_content, flags=re.DOTALL)
    
    soup = BeautifulSoup(html_content, 'html.parser')

    # 【処理1】画像 (img) とそれを囲むリンク (a) の処理
    for img in soup.find_all('img'):
        img_url = img.get('src')
        if not img_url: continue

        target_url = get_original_image_url(img_url)
        
        # 親が<a>タグの場合の特別な処理（誤ったimgのsrcを無視して<a>のhrefを採用する）
        parent = img.parent
        if parent and parent.name == 'a':
            href = parent.get('href', '')
            if re.search(r'\.(jpe?g|png|gif|webp)(\?.*)?$', href, re.IGNORECASE):
                target_url = href
                parent.unwrap() # aタグを剥がす

        filename = download_image(target_url, media_dir, unix_time)
        if filename:
            img.replace_with(f"{{{{:blog:{namespace_path}:{filename}|}}}}")

    # 【処理2】リンクのみで配置された画像の処理と、通常のリンク処理
    for a in soup.find_all('a'):
        href = a.get('href', '')
        
        # WP内の画像への直リンクか判定 (古いドメイン等も考慮し、拡張子で判断)
        if re.search(r'\.(jpe?g|png|gif|webp)(\?.*)?$', href, re.IGNORECASE):
            filename = download_image(href, media_dir, unix_time)
            if filename:
                a.replace_with(f"{{{{:blog:{namespace_path}:{filename}|}}}}")
                continue
                
        text = a.get_text(strip=True)
        if text:
            a.replace_with(f"[[{href}|{text}]]")
        else:
            a.replace_with(f"[[{href}]]")

    # 【処理3】装飾タグの変換
    for strong in soup.find_all(['strong', 'b']):
        strong.insert_before('**')
        strong.insert_after('**')
        strong.unwrap()

    for em in soup.find_all(['em', 'i']):
        em.insert_before('//')
        em.insert_after('//')
        em.unwrap()

    # 【処理4】テーブル (table) をDokuWiki形式に変換
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

    # 【処理5】見出し、リスト、段落の変換
    for h in soup.find_all(['h1', 'h2', 'h3', 'h4', 'h5', 'h6']):
        level = int(h.name[1])
        eq = '=' * (7 - level)
        h.insert_before(f'\n\n{eq} ')
        h.insert_after(f' {eq}\n\n')
        h.unwrap()

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
        
        # タイトルもHTMLエンティティをデコード
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

        post_page_dir = os.path.join(PAGES_BASE, namespace)
        post_media_dir = os.path.join(MEDIA_BASE, namespace)
        ensure_dir(post_page_dir)
        ensure_dir(post_media_dir)

        html_content = post['content']['rendered']
        dokuwiki_content = convert_html_to_dokuwiki(html_content, post_media_dir, namespace, unix_time)

        txt_path = os.path.join(post_page_dir, f"{title_clean}.txt")
        final_content = f"~~META: date created = {date_str} ~~\n\n====== {title_raw} ======\n\n{dokuwiki_content}"

        with open(txt_path, 'w', encoding='utf-8') as f:
            f.write(final_content)
            
        os.utime(txt_path, (unix_time, unix_time))
        print(f"出力完了: [blog:{namespace}:{title_clean}] ({status})")

        # --- リダイレクト情報の収集 ---
        wp_link = post.get('link', '')
        if not INCLUDE_FQDN:
            wp_link = urlparse(wp_link).path
        
        redirect_list.append(f"301 {wp_link} blog:{namespace}:{title_clean}")

    # --- リダイレクト設定ファイルの出力 ---
    redirects_path = os.path.join(DATA_DIR, 'redirects.txt')
    with open(redirects_path, 'w', encoding='utf-8') as f:
        f.write("\n".join(redirect_list) + "\n")
    print(f"\nリダイレクト設定ファイルを作成しました: {redirects_path}")

if __name__ == '__main__':
    main()