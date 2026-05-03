import os
import re
import json
import glob
import argparse
import configparser
from PIL import Image

# ==========================================
# 1. 設定の読み込みと準備
# ==========================================
script_dir = os.path.dirname(os.path.abspath(__file__))
config = configparser.ConfigParser()
config.read(os.path.join(script_dir, 'config.ini'), encoding='utf-8')

DATA_DIR = config['DokuWiki']['data_dir']
PAGES_BASE = os.path.join(DATA_DIR, 'pages', 'blog')
MEDIA_BASE = os.path.join(DATA_DIR, 'media', 'blog')
IMAGE_MAP_PATH = os.path.join(script_dir, 'image_map.json')

# ==========================================
# 共通ユーティリティ関数
# ==========================================
def get_all_txt_files():
    """pages配下のすべての.txtファイルのパスを取得"""
    return glob.glob(os.path.join(PAGES_BASE, '**', '*.txt'), recursive=True)

def get_all_media_files():
    """media配下のすべてのファイルのパスを取得"""
    return glob.glob(os.path.join(MEDIA_BASE, '**', '*.*'), recursive=True)

def update_text_file(filepath, callback):
    """テキストファイルを読み込み、コールバック関数で変更があれば保存する"""
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()
    
    new_content = callback(content)
    
    if new_content != content:
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(new_content)
        return True
    return False

def load_image_map():
    if os.path.exists(IMAGE_MAP_PATH):
        with open(IMAGE_MAP_PATH, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {}

def save_image_map(mapping):
    with open(IMAGE_MAP_PATH, 'w', encoding='utf-8') as f:
        json.dump(mapping, f, ensure_ascii=False, indent=4)

# ==========================================
# 処理機能群
# ==========================================
def cmd_switch(target_type):
    """テキスト内の画像参照をオリジナル/加工済みに切り替える"""
    mapping = load_image_map()
    if not mapping:
        print("image_map.json が見つからないか空です。")
        return

    updated_count = 0
    txt_files = get_all_txt_files()

    def replace_func(content):
        for processed_name, original_name in mapping.items():
            if target_type == 'original':
                old, new = processed_name, original_name
            else:
                old, new = original_name, processed_name
            
            # {{:blog:ns:old_name|}} 等を安全に置換する正規表現
            pattern = r'(\{\{:blog:[^:]+?:)' + re.escape(old) + r'([?|}])'
            content = re.sub(pattern, r'\g<1>' + new + r'\g<2>', content)
        return content

    for filepath in txt_files:
        if update_text_file(filepath, replace_func):
            updated_count += 1
            
    print(f"{updated_count} 件のテキストファイルの参照を {target_type} に切り替えました。")

def cmd_clean():
    """テキストファイルで参照されていない画像を抽出し、対話的に削除する"""
    txt_files = get_all_txt_files()
    referenced_images = set()

    # 全テキストファイルから参照されている画像ファイル名を抽出
    for filepath in txt_files:
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()
            # {{:blog:namespace:filename.ext?700|alt}} から filename.ext を抽出
            matches = re.findall(r'\{\{:blog:[^:]+?:([^?|}]+)', content)
            referenced_images.update(matches)

    media_files = get_all_media_files()
    unreferenced_files = []

    for filepath in media_files:
        filename = os.path.basename(filepath)
        if filename not in referenced_images:
            unreferenced_files.append(filepath)

    if not unreferenced_files:
        print("削除対象の未参照画像はありません。メディアフォルダは綺麗です！")
        return

    print(f"\n以下の {len(unreferenced_files)} 個のファイルがどのページからも参照されていません：")
    for f in unreferenced_files:
        print(f"  - {os.path.basename(f)}")

    ans = input("\nこれらのファイルを完全に削除しますか？ [y/N]: ").strip().lower()
    if ans == 'y':
        mapping = load_image_map()
        map_changed = False
        
        for filepath in unreferenced_files:
            filename = os.path.basename(filepath)
            os.remove(filepath)
            
            # JSONマップからも削除
            if filename in mapping:
                del mapping[filename]
                map_changed = True
            # 値(オリジナル)側として登録されている場合の削除
            keys_to_delete = [k for k, v in mapping.items() if v == filename]
            for k in keys_to_delete:
                del mapping[k]
                map_changed = True

        if map_changed:
            save_image_map(mapping)
            
        print("削除が完了し、image_map.json も更新しました。")
    else:
        print("削除をキャンセルしました。")

def cmd_convert_webp(keep_original):
    """png/jpgをWebPに一括変換し、txtとjsonも書き換える"""
    media_files = get_all_media_files()
    mapping = load_image_map()
    txt_files = get_all_txt_files()
    
    converted_count = 0
    map_changed = False

    # テキスト置換用の一括辞書 (古いファイル名 -> 新しいファイル名)
    rename_dict = {}

    for filepath in media_files:
        if filepath.lower().endswith(('.png', '.jpg', '.jpeg')):
            filename = os.path.basename(filepath)
            base_name, ext = os.path.splitext(filename)
            new_filename = base_name + '.webp'
            new_filepath = os.path.splitext(filepath)[0] + '.webp'
            
            # 既に同名のWebPがある場合はスキップ
            if os.path.exists(new_filepath):
                continue

            try:
                with Image.open(filepath) as img:
                    icc = img.info.get('icc_profile')
                    if ext.lower() == '.png':
                        img.save(new_filepath, format='WEBP', lossless=True, icc_profile=icc)
                    else:
                        img.save(new_filepath, format='WEBP', quality=85, icc_profile=icc)
                        
                rename_dict[filename] = new_filename
                converted_count += 1
                
                # 元画像を削除する場合
                if not keep_original:
                    os.remove(filepath)

            except Exception as e:
                print(f"変換失敗 ({filename}): {e}")

    if not rename_dict:
        print("変換可能な画像がありませんでした。")
        return

    # 1. テキストファイルの参照を .webp に書き換え
    txt_updated_count = 0
    def replace_ext_func(content):
        for old_name, new_name in rename_dict.items():
            pattern = r'(\{\{:blog:[^:]+?:)' + re.escape(old_name) + r'([?|}])'
            content = re.sub(pattern, r'\g<1>' + new_name + r'\g<2>', content)
        return content

    for filepath in txt_files:
        if update_text_file(filepath, replace_ext_func):
            txt_updated_count += 1

    # 2. image_map.json のキーと値を .webp に書き換え
    new_mapping = {}
    for k, v in mapping.items():
        new_k = rename_dict.get(k, k)
        new_v = rename_dict.get(v, v)
        new_mapping[new_k] = new_v
        if new_k != k or new_v != v:
            map_changed = True

    if map_changed:
        save_image_map(new_mapping)

    print(f"WebP変換完了: {converted_count} 枚。")
    print(f"関連する {txt_updated_count} 件のテキストファイルと image_map.json を更新しました。")

def cmd_resize_limit(limit_width):
    """指定サイズ以上の画像に ?WIDTH を付与してリサイズ表示させる"""
    media_files = get_all_media_files()
    large_images = set()

    # 指定サイズ以上の画像をリストアップ
    for filepath in media_files:
        try:
            with Image.open(filepath) as img:
                if img.width > limit_width:
                    large_images.add(os.path.basename(filepath))
        except IOError:
            continue # 画像以外のファイルは無視

    if not large_images:
        print(f"横幅 {limit_width}px を超える画像は見つかりませんでした。")
        return

    updated_count = 0
    txt_files = get_all_txt_files()

    def apply_resize_func(content):
        for img_name in large_images:
            # 既にサイズ指定（?数字）がない場合のみ、?LIMIT を付与する
            # 検索対象: {{:blog:ns:filename|alt}} または {{:blog:ns:filename}}
            pattern = r'(\{\{:blog:[^:]+?:' + re.escape(img_name) + r')([|}])'
            replacement = r'\g<1>?' + str(limit_width) + r'\g<2>'
            content = re.sub(pattern, replacement, content)
        return content

    for filepath in txt_files:
        if update_text_file(filepath, apply_resize_func):
            updated_count += 1

    print(f"横幅 {limit_width}px 超えの画像を検知し、{updated_count} 件のテキストファイルにサイズ制限を付与しました。")

# ==========================================
# CLIコマンドのパース
# ==========================================
if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="DokuWiki移行後のデータメンテナンスツール")
    
    parser.add_argument('--switch', choices=['original', 'processed'], 
                        help='テキスト内の画像参照をオリジナル/加工済みに切り替える')
    parser.add_argument('--clean', action='store_true', 
                        help='テキストで参照されていない画像を抽出し削除する')
    parser.add_argument('--convert-webp', action='store_true', 
                        help='PNG/JPGをWebPに一括変換する')
    parser.add_argument('--keep-original', action='store_true', 
                        help='WebP変換時に元画像を削除せず残す (--convert-webp専用)')
    parser.add_argument('--resize-limit', type=int, metavar='WIDTH',
                        help='指定した横幅を超える画像のDokuWiki構文にサイズ制限(?WIDTH)を付与する')

    args = parser.parse_args()

    if args.switch:
        cmd_switch(args.switch)
    elif args.clean:
        cmd_clean()
    elif args.convert_webp:
        cmd_convert_webp(args.keep_original)
    elif args.resize_limit:
        cmd_resize_limit(args.resize_limit)
    else:
        parser.print_help()