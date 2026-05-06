長い時間をかけての調整と改修、本当にお疲れ様でした。
スクリプトが実用に耐えうるレベルになり、無事に移行とメンテナンスの道筋が立ったとのこと、私としても非常に嬉しいです！

今回作成した強力な移行・メンテナンスパイプラインの仕様書として、Markdown形式で `README.md` を作成しました。
後から見返しても使い方や仕組みがすぐに分かるように、設定方法から各コマンドの役割まで詳細にまとめてあります。リポジトリやフォルダのルートに配置してご活用ください。

---

# WordPress to DokuWiki 移行＆メンテナンスツール

WordPressのブログ記事（HTML）をDokuWiki構文（txt）へ変換し、画像のダウンロードと管理、さらに移行後の画像最適化（WebP変換・リサイズ）までを一貫して行うPythonスクリプトセットです。

## 概要
本ツールは、以下の2つのスクリプトで構成されています。

1. **`wp2dokuwiki.py` (抽出・変換スクリプト)**
   WordPressのREST API経由で記事データ（タイトル、本文、カテゴリ等）を取得し、DokuWikiのテキスト構文に変換して保存します。同時に、記事内で使用されている画像を自動ダウンロードし、UUIDを用いた独自のJSONマッピングで一元管理します。
2. **`dokuwiki_post_process.py` (後処理・メンテナンススクリプト)**
   DokuWiki移行後に、ダウンロードした画像をCPUマルチプロセスで高速にWebPへ変換したり、使われていない画像のクリーンアップ、指定サイズを超える画像に対する構文上のサイズ制限付与などを実行します。

---

## 準備と設定

### 1. 動作環境
- Python 3.7 以上
- 必須パッケージのインストール:
  ```bash
  pip install requests beautifulsoup4 Pillow
  ```

### 2. 設定ファイル (`config.ini`)
スクリプトと同じディレクトリに `config.ini` を作成し、以下の内容を自身の環境に合わせて記述します。
```ini
[WordPress]
url = https://your-wordpress-site.com
username = your_username
app_password = xxxx xxxx xxxx xxxx xxxx xxxx

[DokuWiki]
data_dir = ./dokuwiki/data

[Settings]
timezone_offset = 9
include_fqdn_in_redirects = False
use_original_image = True
archive_namespace = archive
use_imagebox_plugin = True
```

#### `[WordPress]` セクション
移行元となるWordPressへのAPIアクセスに関する設定です。
*   **`url`**
    *   **説明:** 移行元のWordPressサイトのトップURLを指定します。末尾のスラッシュ（`/`）はあってもなくても自動で補正されます。
    *   **例:** `[https://your-wordpress-site.com](https://your-wordpress-site.com)`
*   **`username`**
    *   **説明:** WordPressの管理者（または記事の読み取り権限を持つ）ユーザー名を指定します。
    *   **例:** `admin_user`
*   **`app_password`**
    *   **説明:** WordPressの「アプリケーションパスワード」を指定します。通常のログインパスワードとは異なり、WordPressの管理画面（ユーザー ＞ プロフィール）からAPIアクセス専用に発行するパスワードです。空白区切りでもそのまま記述可能です。
    *   **例:** `xxxx xxxx xxxx xxxx xxxx xxxx`

#### `[DokuWiki]` セクション
移行先となるDokuWikiの出力先に関する設定です。
*   **`data_dir`**
    *   **説明:** DokuWikiのデータディレクトリ（`pages` や `media` フォルダが格納されているディレクトリ）へのパスを指定します。相対パス（`./`）または絶対パスで記述します。
    *   **例:** `./dokuwiki/data` （スクリプトと同じ階層にある `dokuwiki` フォルダ内に出力する場合）

#### `[Settings]` セクション
スクリプトの動作や、DokuWiki構文への変換ルールに関する詳細設定です。
*   **`timezone_offset`**
    *   **説明:** WordPressから取得した記事の作成日時（UTC）を現地時間に変換するための時差（時間）を指定します。日本時間（JST）の場合は `9` を指定します。
    *   **例:** `9`
*   **`include_fqdn_in_redirects`**
    *   **説明:** スクリプト完了時に出力されるリダイレクト用リスト（`redirects.txt`）の「転送元URL」に、ドメイン名を含めるかどうかを指定します。
        *   `True`: ドメイン名を含む完全なURL（例: `[https://example.com/2020/post/](https://example.com/2020/post/)`）で出力されます。
        *   `False`: ドメイン名を省いたパスのみ（例: `/2020/post/`）で出力されます。`.htaccess` 等でリダイレクトを書く場合は `False` が便利です。
*   **`use_original_image`**
    *   **説明:** 画像を取得・配置する際、WordPressが自動生成したリサイズ版の画像（ファイル名に `-scaled` や `-300x200` 等が付くもの）ではなく、元のオリジナル画像を優先して使用するかどうかを指定します。
        *   `True`: オリジナル画像を優先してDokuWiki構文を生成します（画質を重視する場合推奨）。
        *   `False`: HTMLに記述されていたリサイズ画像のURLをそのまま使用します。
*   **`archive_namespace`**
    *   **説明:** 後処理スクリプト（`dokuwiki_post_process.py`）で `--archive` コマンドを実行した際、どの記事からも参照されていない「未使用画像」を退避（移動）させるためのメディアネームスペース名（フォルダ名）を指定します。
    *   **例:** `archive` （この場合、`media/blog/archive/` フォルダに移動されます）
*   **`use_imagebox_plugin`**
    *   **説明:** DokuWikiの「Plugin imagebox」を利用している環境向けの設定です。
        *   `True`: WordPress側でキャプションが設定されていた画像に対して、`[ {{:namespace:image.jpg|キャプション}} ]` のように `[ ]` で囲んだ専用構文を出力します。
        *   `False`: キャプションの有無に関わらず、通常のDokuWiki画像構文 `{{:namespace:image.jpg|キャプション}}` を出力します。

---

## 1. 抽出スクリプト (`wp2dokuwiki.py`)

WordPressから記事と画像を吸い出し、DokuWikiの所定のディレクトリへ展開します。

### 実行方法
```bash
python wp2dokuwiki.py
```

### 主な機能と仕様
- **API通信:** WordPress REST APIを利用して記事を取得。ステータス（公開、下書き、非公開）に応じてネームスペースを自動振り分けします。
- **HTMLの解体とDokuWiki構文変換:** BeautifulSoupを用いてタグを解析します。WordPress特有のギャラリー構造（`wp-block-gallery` や `ul > li`）を解体し、綺麗な改行を伴うDokuWikiの画像参照記法へ変換します。
- **画像の管理 (`image_map.json`):** 画像のオリジナルURLを基準にUUIDを発行し、ハッシュ（MD5）で変更履歴を管理します。リサイズ画像とオリジナル画像の関係性もこのファイルに記録されます。
- **リダイレクトリスト作成:** 旧WordPress記事のURLから新しいDokuWikiのページIDへのマッピングを `redirects.txt` として出力します（`.htaccess` 等でのリダイレクト設定用）。

---

## 2. 後処理スクリプト (`dokuwiki_post_process.py`)

移行した画像データの軽量化や、不要ファイルの整理を行います。
コマンドライン引数（オプション）を指定して実行します。

### 実行方法
```bash
python dokuwiki_post_process.py [オプション]
```

### オプション一覧と使い方

#### 【WebP一括変換グループ】
CPUのマルチプロセスを利用し、画像を高速にWebP形式（無劣化または高品質）に変換します。変換後、テキストファイル内の画像参照記述とJSONマップを自動更新します。

- `--convert-webp`
  WebP一括変換を実行します。
- `--target-format {all,jpg,png}`
  変換対象の拡張子を指定します。（デフォルト: `all`）
- `--referenced-only`
  DokuWikiのテキストファイル内で**実際に参照されている画像のみ**を変換対象にします。
- `--keep-original`
  変換後も元のJPEG/PNG画像を削除せずに残します。また、拡張子（例: `.jpg` なのに中身が PNG）の不一致を検知した場合は自動でリネーム（訂正）します。
- `--verbose`
  プログレスバーの代わりに、画像1枚ごとの変換サイズログ（「成功/失敗」と「削減サイズ」等）をリアルタイムに詳細出力します。
- `--max-workers NUM`
  マルチプロセスの最大コア数を指定します。未指定時はOSの全論理コアを使用して最速で処理します。

**実行例:** （使われている画像だけを、元画像を残さずに全コアでWebP化し、詳細ログを表示する）
```bash
python dokuwiki_post_process.py --convert-webp --referenced-only --verbose
```

#### 【表示サイズ制限グループ】
指定した横幅を超える巨大な画像に対し、DokuWiki構文のサイズ制限（`?WIDTH`）を一括で付与します。他記事の同名ファイルへの誤爆を防ぐ厳密なネームスペースマッチングを行い、テキスト更新時はファイルのタイムスタンプ（更新日時）を維持します。

- `--resize-limit WIDTH`
  指定した横幅(px)を超える画像を検知し、テキストにサイズ制限を付与します。
- `--overwrite-resize {width-only,keep-ratio}`
  すでにサイズ指定（例: `?300x200`）が存在する場合の上書き挙動を指定します。
  - `width-only`: 縦指定を無視し、横幅のみで上書きします。
  - `keep-ratio`: 既存の縦横比を計算し、新しい横幅に合わせて縦指定も再計算して上書きします。

**実行例:** （横幅700pxを超える画像を700pxに制限し、既存サイズがあればアスペクト比を維持して上書きする）
```bash
python dokuwiki_post_process.py --resize-limit 700 --overwrite-resize keep-ratio
```

#### 【参照切り替え・管理グループ】
未使用画像の整理や、参照の巻き戻しを行います。

- `--clean`
  テキストファイルで1箇所も参照されていない「孤立した画像ファイル」を抽出し、対話プロンプト（y/N）で確認した後に完全に削除します。
- `--archive`
  `--clean` と同様に未参照画像を抽出しますが、削除はせずに `config.ini` で指定したアーカイブ用ネームスペースのディレクトリへ移動（退避）させます。
- `--switch {original,processed,converted}`
  テキストファイル内の画像参照を、JSON履歴に基づいて切り替えます。
  - `original`: ダウンロードした元のオリジナル画像へ参照を戻します。
  - `processed`: WordPress側でリサイズ・加工されていた画像へ参照を切り替えます。
  - `converted`: WebP化された画像へ参照を切り替えます。

---

## 制限事項・注意点
- `dokuwiki_post_process.py` によるテキストファイルの書き換え機能（`--convert-webp`, `--resize-limit`, `--switch` など）は、**必ず `image_map.json` がスクリプトと同じディレクトリに存在し、最新の状態に保たれていること**を前提として動作します。
- 本スクリプト群はDokuWikiの標準記法への変換を目的としています。WordPress側の非常に複雑なショートコードや、特殊なレイアウトプラグイン等には完全に対応できない場合があります。
```