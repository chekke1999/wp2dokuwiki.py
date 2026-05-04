import os
import re
import json
import glob
import shutil
import argparse
import configparser
from PIL import Image, ImageFile

# Pillowのファイル末尾の破損等に対する許容度を上げる
ImageFile.LOAD_TRUNCATED_IMAGES = True

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
ARCHIVE_NS = config.get('Settings', 'archive_namespace', fallback='archive')
ARCHIVE_DIR = os.path.join(MEDIA_BASE, ARCHIVE_NS)

def get_all_txt_files():
    return glob.glob(os.path.join(PAGES_BASE, '**', '*.txt'), recursive=True)

def get_all_media_files():
    return glob.glob(os.path.join(MEDIA_BASE, '**', '*.*'), recursive=True)

def update_text_file(filepath, callback):
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

def update_json_keys_values(mapping, old_name, new_name):
    """ JSONマップ内の特定のファイル名を新しい名前に一括置換する """
    new_mapping = {}
    for k, v_list in mapping.items():
        new_k = new_name if k == old_name else k
        new_v_list = [new_name if item == old_name else item for item in v_list]
        new_mapping[new_k] = new_v_list
    return new_mapping

def get_referenced_images():
    """ 全テキストファイルから参照されている画像ファイル名のSetを取得 """
    txt_files = get_all_txt_files()
    referenced_images = set()
    for filepath in txt_files:
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()
            matches = re.findall(r'\{\{:blog:[^:]+?:([^?|}]+)', content)
            referenced_images.update(matches)
    return referenced_images

# ==========================================
# 処理機能群
# ==========================================
def cmd_switch(target_type):
    mapping = load_image_map()
    if not mapping:
        print("image_map.json が見つからないか空です。")
        return

    updated_count = 0
    txt_files = get_all_txt_files()

    def replace_func(content):
        for original_name, processed_list in mapping.items():
            if not processed_list:
                continue
            
            processed_name = processed_list[0]
            if target_type == 'original':
                old, new = processed_name, original_name
            else:
                old, new = original_name, processed_name
            
            pattern = r'(\{\{:blog:[^:]+?:)' + re.escape(old) + r'([?|}])'
            content = re.sub(pattern, r'\g<1>' + new + r'\g<2>', content)
        return content

    for filepath in txt_files:
        if update_text_file(filepath, replace_func):
            updated_count += 1
            
    print(f"{updated_count} 件のテキストファイルの参照を {target_type} に切り替えました。")

def cmd_clean():
    referenced_images = get_referenced_images()
    media_files = get_all_media_files()
    unreferenced_files = [f for f in media_files if os.path.basename(f) not in referenced_images]

    if not unreferenced_files:
        print("削除対象の未参照画像はありません。")
        return

    print(f"\n以下の {len(unreferenced_files)} 個のファイルが未参照です：")
    for f in unreferenced_files: print(f"  - {os.path.basename(f)}")

    ans = input("\nこれらを完全に削除しますか？ [y/N]: ").strip().lower()
    if ans == 'y':
        mapping = load_image_map()
        map_changed = False
        
        for filepath in unreferenced_files:
            filename = os.path.basename(filepath)
            os.remove(filepath)
            
            if filename in mapping:
                del mapping[filename]
                map_changed = True
            for k, v_list in mapping.items():
                if filename in v_list:
                    v_list.remove(filename)
                    map_changed = True

        mapping = {k: v for k, v in mapping.items() if v}
        if map_changed: save_image_map(mapping)
        print("削除が完了しました。")
    else:
        print("キャンセルしました。")

def cmd_archive():
    """ 未参照画像をアーカイブネームスペースに退避する """
    referenced_images = get_referenced_images()
    media_files = get_all_media_files()
    unreferenced_files = []
    
    # 既にアーカイブディレクトリにあるものは除外
    for f in media_files:
        if os.path.basename(f) not in referenced_images and ARCHIVE_NS not in os.path.dirname(f):
            unreferenced_files.append(f)

    if not unreferenced_files:
        print("アーカイブ対象の未参照画像はありません。")
        return

    if not os.path.exists(ARCHIVE_DIR):
        os.makedirs(ARCHIVE_DIR)

    mapping = load_image_map()
    map_changed = False
    archived_count = 0

    for filepath in unreferenced_files:
        old_filename = os.path.basename(filepath)
        
        # 移動元の親ディレクトリ名をプレフィックスとして付与 (衝突防止)
        parent_ns = os.path.basename(os.path.dirname(filepath))
        new_filename = f"{parent_ns}_{old_filename}"
        new_filepath = os.path.join(ARCHIVE_DIR, new_filename)
        
        shutil.move(filepath, new_filepath)
        
        # JSON内のファイル名も更新 (削除はしない)
        mapping = update_json_keys_values(mapping, old_filename, new_filename)
        map_changed = True
        archived_count += 1

    if map_changed:
        save_image_map(mapping)
    print(f"{archived_count} 件の画像を {ARCHIVE_NS} にアーカイブし、JSONを更新しました。")

def cmd_convert_webp(target_format, referenced_only, keep_original):
    media_files = get_all_media_files()
    mapping = load_image_map()
    txt_files = get_all_txt_files()
    referenced_images = get_referenced_images() if referenced_only else set()
    
    converted_count = 0
    rename_dict = {}

    for filepath in media_files:
        filename = os.path.basename(filepath)
        base_name, ext = os.path.splitext(filename)
        ext_lower = ext.lower()

        # ターゲットフォーマットのフィルタリング
        if target_format == 'jpg' and ext_lower not in ['.jpg', '.jpeg']: continue
        if target_format == 'png' and ext_lower != '.png': continue
        if ext_lower not in ['.png', '.jpg', '.jpeg']: continue
        
        # 参照されている画像のみをフィルタリング
        if referenced_only and filename not in referenced_images: continue

        new_filename = base_name + '.webp'
        new_filepath = os.path.splitext(filepath)[0] + '.webp'
        
        if os.path.exists(new_filepath): continue

        try:
            with Image.open(filepath) as img:
                # 【エラー対応と拡張子訂正】 実際のフォーマットと拡張子が矛盾しているかチェック
                real_format = img.format
                actual_ext = '.jpg' if real_format == 'JPEG' else '.png' if real_format == 'PNG' else ext_lower
                
                # 矛盾があり、かつ元ファイルを保持する場合はリネームして訂正する
                if keep_original and actual_ext != ext_lower and actual_ext in ['.jpg', '.png']:
                    corrected_filename = base_name + actual_ext
                    corrected_filepath = os.path.splitext(filepath)[0] + actual_ext
                    os.rename(filepath, corrected_filepath)
                    mapping = update_json_keys_values(mapping, filename, corrected_filename)
                    # 訂正した名前を置換対象に追加し、ファイルパスも更新
                    rename_dict[filename] = corrected_filename
                    filename = corrected_filename
                    filepath = corrected_filepath
                    print(f"拡張子を訂正しました: {ext_lower} -> {actual_ext} ({filename})")

                icc = img.info.get('icc_profile')
                if real_format == 'PNG':
                    img.save(new_filepath, format='WEBP', lossless=True, icc_profile=icc)
                else:
                    img.save(new_filepath, format='WEBP', quality=85, icc_profile=icc)
                    
            rename_dict[filename] = new_filename
            converted_count += 1
            
            if not keep_original:
                os.remove(filepath)

        except Exception as e:
            print(f"変換失敗 ({filename}): {e}")

    if not rename_dict:
        print("変換可能な画像がありませんでした。")
        return

    # テキストファイルの書き換え
    txt_updated_count = 0
    def replace_ext_func(content):
        for old_name, new_name in rename_dict.items():
            pattern = r'(\{\{:blog:[^:]+?:)' + re.escape(old_name) + r'([?|}])'
            content = re.sub(pattern, r'\g<1>' + new_name + r'\g<2>', content)
        return content

    for filepath in txt_files:
        if update_text_file(filepath, replace_ext_func):
            txt_updated_count += 1

    # image_map.json の一括更新
    for old_name, new_name in rename_dict.items():
        mapping = update_json_keys_values(mapping, old_name, new_name)
    save_image_map(mapping)

    print(f"WebP変換完了: {converted_count} 枚。")
    print(f"関連する {txt_updated_count} 件のテキストファイルと image_map.json を更新しました。")

def cmd_resize_limit(limit_width, overwrite_mode):
    media_files = get_all_media_files()
    large_images = {} # {filename: (width, height)}

    for filepath in media_files:
        try:
            with Image.open(filepath) as img:
                if img.width > limit_width:
                    large_images[os.path.basename(filepath)] = (img.width, img.height)
        except IOError:
            continue

    if not large_images:
        print(f"横幅 {limit_width}px を超える画像は見つかりませんでした。")
        return

    updated_count = 0
    txt_files = get_all_txt_files()

    def apply_resize_func(content):
        for img_name, (w, h) in large_images.items():
            if overwrite_mode:
                # 既にサイズ指定（?数字 または ?数字x数字）があるものを上書き
                pattern = r'(\{\{:blog:[^:]+?:' + re.escape(img_name) + r')(\?\d+(?:x\d+)?)([|}])'
                
                def replacement_func(match):
                    base, existing_query, tail = match.groups()
                    if overwrite_mode == 'width-only':
                        return f"{base}?{limit_width}{tail}"
                    elif overwrite_mode == 'keep-ratio':
                        # 元々縦指定がある場合、新しい幅に合わせて縦も計算
                        if 'x' in existing_query:
                            aspect_ratio = h / w
                            new_height = int(limit_width * aspect_ratio)
                            return f"{base}?{limit_width}x{new_height}{tail}"
                        else:
                            return f"{base}?{limit_width}{tail}"
                    return match.group(0)

                content = re.sub(pattern, replacement_func, content)

            # サイズ指定がないものに新規追加
            pattern_no_size = r'(\{\{:blog:[^:]+?:' + re.escape(img_name) + r')([|}])'
            replacement_no_size = r'\g<1>?' + str(limit_width) + r'\g<2>'
            content = re.sub(pattern_no_size, replacement_no_size, content)
            
        return content

    for filepath in txt_files:
        if update_text_file(filepath, apply_resize_func):
            updated_count += 1

    print(f"横幅 {limit_width}px 超えの画像を検知し、{updated_count} 件のテキストファイルにサイズ制限を適用しました。")

# ==========================================
# CLIコマンドのパース
# ==========================================
if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="DokuWiki移行後のデータメンテナンスツール")
    
    parser.add_argument('--switch', choices=['original', 'processed'], 
                        help='テキスト内の画像参照をオリジナル/加工済みに切り替える')
    
    parser.add_argument('--clean', action='store_true', 
                        help='テキストで参照されていない画像を抽出し削除する')
    
    parser.add_argument('--archive', action='store_true', 
                        help='未参照画像を削除せず、configで指定したネームスペースに退避する')
    
    parser.add_argument('--convert-webp', action='store_true', 
                        help='画像をWebPに一括変換する')
    parser.add_argument('--target-format', choices=['all', 'jpg', 'png'], default='all',
                        help='WebP変換の対象拡張子 (デフォルト: all)')
    parser.add_argument('--referenced-only', action='store_true',
                        help='DokuWikiテキストで参照されている画像のみを変換対象にする')
    parser.add_argument('--keep-original', action='store_true', 
                        help='WebP変換時に元画像を削除せず残す (矛盾する拡張子の訂正も行う)')
    
    parser.add_argument('--resize-limit', type=int, metavar='WIDTH',
                        help='指定した横幅を超える画像のDokuWiki構文にサイズ制限(?WIDTH)を付与する')
    parser.add_argument('--overwrite-resize', choices=['width-only', 'keep-ratio'],
                        help='既存のサイズ指定がある場合の上書き挙動 (width-only: 横幅のみで上書き, keep-ratio: 縦指定があれば比率を計算して上書き)')

    args = parser.parse_args()

    if args.switch:
        cmd_switch(args.switch)
    elif args.clean:
        cmd_clean()
    elif args.archive:
        cmd_archive()
    elif args.convert_webp:
        cmd_convert_webp(args.target_format, args.referenced_only, args.keep_original)
    elif args.resize_limit:
        cmd_resize_limit(args.resize_limit, args.overwrite_resize)
    else:
        parser.print_help()