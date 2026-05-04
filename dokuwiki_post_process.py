import os
import re
import sys
import json
import glob
import shutil
import hashlib
import argparse
import configparser
import concurrent.futures
from PIL import Image, ImageFile

# Pillowのファイル末尾破損等の許容度を上げる
ImageFile.LOAD_TRUNCATED_IMAGES = True

# ==========================================
# 1. 設定と準備
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

# ==========================================
# ユーティリティ関数
# ==========================================
def format_size(size_in_bytes):
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if size_in_bytes < 1024.0:
            return f"{size_in_bytes:.2f} {unit}"
        size_in_bytes /= 1024.0
    return f"{size_in_bytes:.2f} PB"

def print_progress_bar(iteration, total, prefix='', suffix='', decimals=1, length=50, fill='█'):
    percent = ("{0:." + str(decimals) + "f}").format(100 * (iteration / float(total)))
    filled_length = int(length * iteration // total)
    bar = fill * filled_length + '-' * (length - filled_length)
    sys.stdout.write(f'\r{prefix} |{bar}| {percent}% {suffix}')
    sys.stdout.flush()
    if iteration == total:
        print()

def get_file_md5(filepath):
    hash_md5 = hashlib.md5()
    try:
        with open(filepath, "rb") as f:
            for chunk in iter(lambda: f.read(4096), b""):
                hash_md5.update(chunk)
        return hash_md5.hexdigest()
    except:
        return None

def get_all_txt_files():
    return glob.glob(os.path.join(PAGES_BASE, '**', '*.txt'), recursive=True)

def get_all_media_files():
    return glob.glob(os.path.join(MEDIA_BASE, '**', '*.*'), recursive=True)

def load_image_map():
    if os.path.exists(IMAGE_MAP_PATH):
        with open(IMAGE_MAP_PATH, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {"images": {}, "pages": {}}

def save_image_map(data):
    with open(IMAGE_MAP_PATH, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=4)

def update_text_file(filepath, callback):
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()
    new_content = callback(content)
    if new_content != content:
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(new_content)
        return True
    return False

def get_referenced_images():
    txt_files = get_all_txt_files()
    referenced_images = set()
    for filepath in txt_files:
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()
            matches = re.findall(r'\{\{:blog:[^:]+?:([^?|}]+)', content)
            referenced_images.update(matches)
    return referenced_images

def check_and_promote_original(data, group_uuid):
    group = data["images"].get(group_uuid, {})
    if len(group) == 1:
        for md5_key in group:
            group[md5_key]["is_original"] = True

# ==========================================
# 処理機能群
# ==========================================
def cmd_switch(target_type):
    data = load_image_map()
    if not data or "pages" not in data:
        print("image_map.json が見つからないか構造が古いです。")
        return

    updated_count = 0
    txt_files = get_all_txt_files()

    for filepath in txt_files:
        rel_path = os.path.relpath(filepath, PAGES_BASE)
        ns = os.path.dirname(rel_path).replace(os.sep, ':')
        base_name = os.path.splitext(os.path.basename(rel_path))[0]
        page_id = f"blog:{ns}:{base_name}"
        
        if page_id not in data["pages"]:
            continue

        page_info = data["pages"][page_id]
        changed_in_page = False
        
        def replace_func(content):
            nonlocal changed_in_page
            for group_uuid, history_info in page_info.items():
                current_file = history_info.get("current")
                target_file = history_info.get("history", {}).get(target_type)
                
                if current_file and target_file and current_file != target_file:
                    pattern = r'(\{\{:blog:[^:]+?:)' + re.escape(current_file) + r'([?|}])'
                    new_content = re.sub(pattern, r'\g<1>' + target_file + r'\g<2>', content)
                    if new_content != content:
                        content = new_content
                        page_info[group_uuid]["current"] = target_file
                        changed_in_page = True
            return content

        if update_text_file(filepath, replace_func):
            updated_count += 1
            
    if updated_count > 0:
        save_image_map(data)
        
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
        data = load_image_map()
        map_changed = False
        
        for filepath in unreferenced_files:
            filename = os.path.basename(filepath)
            os.remove(filepath)
            
            for group_uuid, images in list(data["images"].items()):
                md5_to_delete = [md5 for md5, info in images.items() if info["filename"] == filename]
                for md5 in md5_to_delete:
                    del images[md5]
                    map_changed = True
                
                if md5_to_delete:
                    check_and_promote_original(data, group_uuid)
                    
                if not images:
                    del data["images"][group_uuid]

        if map_changed: save_image_map(data)
        print("削除が完了しました。")
    else:
        print("キャンセルしました。")


def cmd_archive():
    referenced_images = get_referenced_images()
    media_files = get_all_media_files()
    unreferenced_files = []
    
    for f in media_files:
        if os.path.basename(f) not in referenced_images and ARCHIVE_NS not in os.path.dirname(f):
            unreferenced_files.append(f)

    if not unreferenced_files:
        print("アーカイブ対象の未参照画像はありません。")
        return

    if not os.path.exists(ARCHIVE_DIR):
        os.makedirs(ARCHIVE_DIR)

    data = load_image_map()
    map_changed = False
    archived_count = 0

    for filepath in unreferenced_files:
        old_filename = os.path.basename(filepath)
        parent_ns = os.path.basename(os.path.dirname(filepath))
        new_filename = f"{parent_ns}_{old_filename}"
        new_filepath = os.path.join(ARCHIVE_DIR, new_filename)
        
        shutil.move(filepath, new_filepath)
        
        for group_uuid, images in data["images"].items():
            for md5, info in images.items():
                if info["filename"] == old_filename:
                    info["filename"] = new_filename
                    map_changed = True
                    break
                    
        for page_id, groups in data.get("pages", {}).items():
            for group_uuid, history_info in groups.items():
                if history_info.get("current") == old_filename:
                    history_info["current"] = new_filename
                    map_changed = True
                for k, v in history_info.get("history", {}).items():
                    if v == old_filename:
                        history_info["history"][k] = new_filename
                        map_changed = True

        archived_count += 1

    if map_changed:
        save_image_map(data)
    print(f"{archived_count} 件の画像を {ARCHIVE_NS} にアーカイブしました。")


# --- 並列処理のワーカー関数 ---
def encode_single_image(filepath, base_name, ext_lower):
    """ マルチプロセスで呼ばれるエンコード関数 (I/O・CPUバウンド処理のみ担当) """
    filename = os.path.basename(filepath)
    new_filepath = os.path.splitext(filepath)[0] + '.webp'
    
    result = {
        "success": False,
        "filename": filename,
        "filepath": filepath,
        "new_filepath": new_filepath,
        "orig_md5": get_file_md5(filepath),
        "orig_size": os.path.getsize(filepath),
        "new_md5": None,
        "new_size": 0,
        "real_format": None,
        "error_msg": ""
    }

    try:
        with Image.open(filepath) as img:
            result["real_format"] = img.format
            icc = img.info.get('icc_profile')
            
            if img.format == 'PNG':
                img.save(new_filepath, format='WEBP', lossless=True, icc_profile=icc)
            else:
                img.save(new_filepath, format='WEBP', quality=85, icc_profile=icc)
                
        result["new_md5"] = get_file_md5(new_filepath)
        result["new_size"] = os.path.getsize(new_filepath)
        result["success"] = True
    except Exception as e:
        result["error_msg"] = str(e)

    return result


def cmd_convert_webp(target_format, referenced_only, keep_original, verbose, max_workers):
    media_files = get_all_media_files()
    data = load_image_map()
    txt_files = get_all_txt_files()
    referenced_images = get_referenced_images() if referenced_only else set()
    
    target_tasks = []
    
    # 対象ファイルの絞り込み
    for filepath in media_files:
        filename = os.path.basename(filepath)
        base_name, ext = os.path.splitext(filename)
        ext_lower = ext.lower()

        if target_format == 'jpg' and ext_lower not in ['.jpg', '.jpeg']: continue
        if target_format == 'png' and ext_lower != '.png': continue
        if ext_lower not in ['.png', '.jpg', '.jpeg']: continue
        if referenced_only and filename not in referenced_images: continue

        new_filepath = os.path.splitext(filepath)[0] + '.webp'
        if os.path.exists(new_filepath): continue
        
        target_tasks.append((filepath, base_name, ext_lower))

    if not target_tasks:
        print("変換対象の画像がありませんでした。")
        return

    total_files = len(target_tasks)
    worker_str = str(max_workers) if max_workers else "Auto(全コア)"
    print(f"WebP変換を開始します [対象: {total_files}件, コア数制限: {worker_str}]...")
    
    converted_count = 0
    total_original_size = 0
    total_converted_size = 0
    rename_dict = {}
    
    # マルチプロセスによる並列エンコード処理
    with concurrent.futures.ProcessPoolExecutor(max_workers=max_workers) as executor:
        future_to_task = {
            executor.submit(encode_single_image, path, bname, ext_l): (path, bname, ext_l) 
            for path, bname, ext_l in target_tasks
        }
        
        completed_count = 0
        for future in concurrent.futures.as_completed(future_to_task):
            res = future.result()
            completed_count += 1
            
            filename = res["filename"]
            filepath = res["filepath"]
            new_filepath = res["new_filepath"]
            base_name, ext = os.path.splitext(filename)
            ext_lower = ext.lower()

            if not res["success"]:
                if verbose:
                    print(f"[エラー] 変換失敗 ({filename}): {res['error_msg']}")
                else:
                    # プログレスバー表示中に出力すると崩れるため、強制改行して表示
                    print(f"\n[エラー] 変換失敗 ({filename}): {res['error_msg']}")
                continue

            # UUIDとオリジナルMD5の特定
            orig_md5 = res["orig_md5"]
            group_uuid = None
            for uid, images in data["images"].items():
                if orig_md5 in images:
                    group_uuid = uid
                    break

            actual_ext = '.jpg' if res["real_format"] == 'JPEG' else '.png' if res["real_format"] == 'PNG' else ext_lower
            
            # 拡張子訂正
            if keep_original and actual_ext != ext_lower and actual_ext in ['.jpg', '.png']:
                corrected_filename = base_name + actual_ext
                corrected_filepath = os.path.splitext(filepath)[0] + actual_ext
                os.rename(filepath, corrected_filepath)
                
                if group_uuid and orig_md5:
                    data["images"][group_uuid][orig_md5]["filename"] = corrected_filename
                for page_id, groups in data.get("pages", {}).items():
                    for uid, history_info in groups.items():
                        if history_info.get("current") == filename:
                            history_info["current"] = corrected_filename
                        for k, v in history_info.get("history", {}).items():
                            if v == filename:
                                history_info["history"][k] = corrected_filename

                rename_dict[filename] = corrected_filename
                filename = corrected_filename
                filepath = corrected_filepath

            new_filename = os.path.basename(new_filepath)
            rename_dict[filename] = new_filename
            converted_count += 1
            total_original_size += res["orig_size"]
            total_converted_size += res["new_size"]

            # 【変更点】 リアルタイムでのログ出力
            if verbose:
                print(f"[成功] {filename} ({format_size(res['orig_size'])}) -> {new_filename} ({format_size(res['new_size'])})")
            else:
                print_progress_bar(completed_count, total_files, prefix='Progress:', suffix='Complete', length=50)

            # JSONへWebPの登録
            if group_uuid and orig_md5 and res["new_md5"]:
                conv_type = "lossless" if res["real_format"] == 'PNG' else "lossy"
                data["images"][group_uuid][res["new_md5"]] = {
                    "filename": new_filename,
                    "filesize": res["new_size"],
                    "is_original": False,
                    "converted_from_md5": orig_md5,
                    "conversion_type": conv_type
                }
                for page_id, groups in data.get("pages", {}).items():
                    if group_uuid in groups:
                        groups[group_uuid]["history"]["converted"] = new_filename
            
            # 元画像の削除と昇格チェック
            if not keep_original:
                os.remove(filepath)
                if group_uuid and orig_md5:
                    del data["images"][group_uuid][orig_md5]
                    check_and_promote_original(data, group_uuid)

    if converted_count == 0:
        print("\n変換に成功した画像はありませんでした。")
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

    # DokuWiki上で参照が切り替わったため、pages の current も更新
    for page_id, groups in data.get("pages", {}).items():
        for group_uuid, history_info in groups.items():
            current = history_info.get("current")
            if current in rename_dict:
                history_info["current"] = rename_dict[current]

    save_image_map(data)

    # サマリー
    saved_size = total_original_size - total_converted_size
    print("\n--- 変換サマリー ---")
    print(f"変換完了: {converted_count} / {total_files} 枚")
    print(f"テキスト更新: {txt_updated_count} 件")
    print(f"変換前合計サイズ: {format_size(total_original_size)}")
    print(f"変換後合計サイズ: {format_size(total_converted_size)}")
    if saved_size > 0:
        print(f"削減された容量:   {format_size(saved_size)}")
    print("--------------------")

def cmd_resize_limit(limit_width, overwrite_mode):
    media_files = get_all_media_files()
    large_images = {} 

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
                pattern = r'(\{\{:blog:[^:]+?:' + re.escape(img_name) + r')(\?\d+(?:x\d+)?)([|}])'
                def replacement_func(match):
                    base, existing_query, tail = match.groups()
                    if overwrite_mode == 'width-only':
                        return f"{base}?{limit_width}{tail}"
                    elif overwrite_mode == 'keep-ratio':
                        if 'x' in existing_query:
                            aspect_ratio = h / w
                            new_height = int(limit_width * aspect_ratio)
                            return f"{base}?{limit_width}x{new_height}{tail}"
                        else:
                            return f"{base}?{limit_width}{tail}"
                    return match.group(0)
                content = re.sub(pattern, replacement_func, content)

            pattern_no_size = r'(\{\{:blog:[^:]+?:' + re.escape(img_name) + r')([|}])'
            replacement_no_size = r'\g<1>?' + str(limit_width) + r'\g<2>'
            content = re.sub(pattern_no_size, replacement_no_size, content)
            
        return content

    for filepath in txt_files:
        if update_text_file(filepath, apply_resize_func):
            updated_count += 1

    print(f"横幅 {limit_width}px 超えの画像を検知し、{updated_count} 件のテキストにサイズ制限を適用しました。")

# ==========================================
# CLI設定 (RawTextHelpFormatterを使用し、グループ化して表示)
# ==========================================
if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        description="DokuWiki移行後のデータメンテナンスツール",
        formatter_class=argparse.RawTextHelpFormatter
    )
    
    # 参照管理グループ
    grp_ref = parser.add_argument_group('参照切り替え・管理オプション')
    grp_ref.add_argument('--switch', choices=['original', 'processed', 'converted'], 
                        help='テキスト内の画像参照を切り替える\n(original: オリジナル, processed: 加工済み, converted: WebP)')
    grp_ref.add_argument('--clean', action='store_true', 
                        help='テキストで参照されていない画像を抽出し、対話的に削除する')
    grp_ref.add_argument('--archive', action='store_true', 
                        help='未参照画像を削除せず、configで指定した退避先ネームスペースへ移動する')
    
    # WebP変換グループ
    grp_webp = parser.add_argument_group('WebP一括変換オプション')
    grp_webp.add_argument('--convert-webp', action='store_true', 
                        help='画像をマルチプロセスでWebPに一括変換し、テキストやJSONを自動更新する')
    grp_webp.add_argument('--target-format', choices=['all', 'jpg', 'png'], default='all',
                        help='(convert-webp用) 変換対象の拡張子 (デフォルト: all)')
    grp_webp.add_argument('--referenced-only', action='store_true',
                        help='(convert-webp用) DokuWikiで参照されている画像のみを変換対象にする')
    grp_webp.add_argument('--keep-original', action='store_true', 
                        help='(convert-webp用) 元画像を削除せずに残す (拡張子が矛盾している場合の自動訂正も行う)')
    grp_webp.add_argument('--verbose', action='store_true', 
                        help='(convert-webp用) プログレスバーの代わりに、画像1枚ごとの変換サイズログを詳細に出力する')
    grp_webp.add_argument('--max-workers', type=int, metavar='NUM',
                        help='(convert-webp用) 並列処理の最大コア数を指定する (未指定時はOSの全論理コアを使用)')

    # リサイズ制限グループ
    grp_resize = parser.add_argument_group('表示サイズ制限オプション')
    grp_resize.add_argument('--resize-limit', type=int, metavar='WIDTH',
                        help='指定した横幅(px)を超える画像に対し、DokuWiki構文でサイズ制限(?WIDTH)を付与する')
    grp_resize.add_argument('--overwrite-resize', choices=['width-only', 'keep-ratio'],
                        help='(resize-limit用) 既存のサイズ指定がある場合の上書き挙動\n'
                             '  - width-only : 縦指定を無視し、横幅のみで上書きする\n'
                             '  - keep-ratio : 既存の縦横比を計算し、新しい横幅に合わせて縦指定も上書きする')

    args = parser.parse_args()

    # コマンドの実行判定
    if args.switch:
        cmd_switch(args.switch)
    elif args.clean:
        cmd_clean()
    elif args.archive:
        cmd_archive()
    elif args.convert_webp:
        cmd_convert_webp(args.target_format, args.referenced_only, args.keep_original, args.verbose, args.max_workers)
    elif args.resize_limit:
        cmd_resize_limit(args.resize_limit, args.overwrite_resize)
    else:
        parser.print_help()