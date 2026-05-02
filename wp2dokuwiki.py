import os
import re
import configparser
import requests
from urllib.parse import urlparse, unquote
from bs4 import BeautifulSoup
from datetime import datetime

# ==========================================
# 1. 設定の読み込みと準備
# ==========================================
config = configparser.ConfigParser()
config.read('config.ini', encoding='utf-8')

WP_URL = config['WordPress']['url']
WP_USER = config['WordPress']['username']
WP_PASS = config['WordPress']['app_password']
DATA_DIR = config['DokuWiki']['data_dir']

API_BASE = f"{WP_URL}/wp-json/wp/v2"
AUTH = (WP_USER, WP_PASS)

# DokuWikiのパス設定
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
            categories[cat['id']] = cat['slug'] # または cat['name'] (今回はURLセーフなslugを採用)
        url = res.links.get('next', {}).get('url')
    return categories

def get_posts():
    print("投稿データを取得中...")
    posts = []
    # 公開、下書き、非公開をすべて取得
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
# 3. テキストとHTMLの変換・整形関数
# ==========================================
def sanitize_filename(title):
    # 【】を_に変換し、DokuWikiで使えない記号を削除
    clean = title.replace('【', '_').replace('】', '_')
    clean = re.sub(r'[\\/*?:"<>|#]+', '', clean)
    clean = re.sub(r'_+', '_', clean) # 連続するアンダースコアを1つに
    clean = clean.strip('_').strip()
    return clean

def get_original_image_url(url):
    # WPのリサイズ接尾辞 (例: -150x150.jpg) を削除してオリジナルURLを推測
    return re.sub(r'-\d+x\d+(\.[a-zA-Z]+)$', r'\1', url)

def download_image(img_url, save_dir):
    try:
        # URLをデコードしてファイル名を取得
        parsed_url = urlparse(img_url)
        filename = unquote(os.path.basename(parsed_url.path))
        save_path = os.path.join(save_dir, filename)
        
        if not os.path.exists(save_path):
            res = requests.get(img_url, stream=True)
            if res.status_code == 200:
                with open(save_path, 'wb') as f:
                    for chunk in res.iter_content(1024):
                        f.write(chunk)
                return filename
        return filename
    except Exception as e:
        print(f"画像ダウンロード失敗 ({img_url}): {e}")
        return None

def convert_html_to_dokuwiki(html_content, media_dir, namespace_path):
    # Gutenbergのコメントブロックを削除
    html_content = re.sub(r'<!-- wp:.*?-->', '', html_content, flags=re.DOTALL)
    html_content = re.sub(r'<!-- /wp:.*?-->', '', html_content, flags=re.DOTALL)
    
    soup = BeautifulSoup(html_content, 'html.parser')
    dokuwiki_text = ""

    # 画像の処理と置換
    for img in soup.find_all('img'):
        img_url = img.get('src')
        if not img_url:
            continue
        original_url = get_original_image_url(img_url)
        filename = download_image(original_url, media_dir)
        
        if filename:
            # DokuWikiの画像構文に置換 (パイプ含む、中央/右寄せなし)
            doku_img = f"{{{{:blog:{namespace_path}:{filename}|}}}}"
            img.replace_with(doku_img)

    # シンプルなHTML要素をDokuWiki構文に変換（簡易版）
    for p in soup.find_all('p'):
        p.insert_before('\n\n')
        p.insert_after('\n\n')
        p.unwrap()
        
    for h in soup.find_all(['h1', 'h2', 'h3', 'h4', 'h5', 'h6']):
        level = int(h.name[1])
        eq = '=' * (7 - level) # h1=======, h2======, h3====== ...
        h.insert_before(f'\n\n{eq} ')
        h.insert_after(f' {eq}\n\n')
        h.unwrap()

    for strong in soup.find_all(['strong', 'b']):
        strong.insert_before('**')
        strong.insert_after('**')
        strong.unwrap()

    for em in soup.find_all(['em', 'i']):
        em.insert_before('//')
        em.insert_after('//')
        em.unwrap()

    for a in soup.find_all('a'):
        href = a.get('href', '')
        text = a.get_text()
        if text:
            a.replace_with(f"[[{href}|{text}]]")
        else:
            a.replace_with(f"[[{href}]]")

    for ul in soup.find_all('ul'):
        for li in ul.find_all('li', recursive=False):
            li.insert_before('  * ')
            li.insert_after('\n')
            li.unwrap()
        ul.insert_before('\n')
        ul.insert_after('\n')
        ul.unwrap()

    # BeautifulSoupでテキスト抽出後、余分な改行を整理
    text = soup.get_text()
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()

# ==========================================
# 4. メイン処理
# ==========================================
def main():
    cat_map = get_categories()
    posts = get_posts()
    
    print(f"\n合計 {len(posts)} 件の投稿を処理します...")
    
    for post in posts:
        title_raw = post['title']['rendered']
        title_clean = sanitize_filename(title_raw)
        if not title_clean:
            title_clean = str(post['id'])
            
        status = post['status']
        date_str = post['date'].replace('T', ' ') # 2023-01-01T12:00:00 -> 2023-01-01 12:00:00
        
        # ネームスペースの決定
        if status == 'draft':
            namespace = "drafts"
        elif status == 'private':
            namespace = "private"
        else:
            # 複数カテゴリがある場合は最初の1つを採用
            cat_ids = post.get('categories', [])
            if cat_ids and cat_ids[0] in cat_map:
                namespace = cat_map[cat_ids[0]]
            else:
                namespace = "uncategorized"

        # DokuWiki上の保存先ディレクトリ
        post_page_dir = os.path.join(PAGES_BASE, namespace)
        post_media_dir = os.path.join(MEDIA_BASE, namespace)
        ensure_dir(post_page_dir)
        ensure_dir(post_media_dir)

        # HTMLをDokuWiki構文に変換しつつ画像をダウンロード
        html_content = post['content']['rendered']
        dokuwiki_content = convert_html_to_dokuwiki(html_content, post_media_dir, namespace)

        # DokuWikiファイルの作成
        txt_path = os.path.join(post_page_dir, f"{title_clean}.txt")
        
        # METAタグの付与
        final_content = f"~~META: date created = {date_str} ~~\n\n====== {title_raw} ======\n\n{dokuwiki_content}"

        with open(txt_path, 'w', encoding='utf-8') as f:
            f.write(final_content)
            
        print(f"出力完了: [blog:{namespace}:{title_clean}] ({status})")

if __name__ == '__main__':
    main()