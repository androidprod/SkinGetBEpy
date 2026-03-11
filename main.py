"""
main.py - SkinGetBE Python 版 エントリポイント
Mirrors: main.cpp

使用方法:
  python main.py [--config config.json]

Minecraft BE クライアントから {host}:{port} に接続すると、
プレイヤーのスキンが ./skins/ ディレクトリに保存されます。
"""
import sys
import os
import json
import argparse
import random

# プロジェクトルートを sys.path に追加
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from util.logger import log, Logger
from bedrock.skin import save_skin

log = Logger("Main")


def load_config(path: str) -> dict:
    """config.json を読み込む"""
    default = {
        "host": "0.0.0.0",
        "port": 19132,
        "output_dir": "skins",
        "protocol": 776,
        "game_version": "1.21.0",
        "debug": False,
    }
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            default.update(loaded)
            log.info(f"設定ファイル読み込み: {path}")
        except Exception as e:
            log.warn(f"設定ファイル読み込み失敗 ({e}), デフォルト設定を使用")
    else:
        log.info("設定ファイルが見つかりません。デフォルト設定を使用します")
    return default


def on_skin_received(skin_data, addr: tuple, output_dir: str):
    """スキン受信コールバック"""
    log.skin(f"スキン取得完了! プレイヤー: {skin_data.player_name} ({addr[0]}:{addr[1]})")
    saved = save_skin(skin_data, output_dir)
    if saved:
        log.ok(f"保存ファイル:")
        for path in saved:
            log.ok(f"  → {os.path.abspath(path)}")
    else:
        log.error("スキンの保存に失敗しました")


def print_banner():
    banner = r"""
  ____  _    _       _____      _   ____  _____
 / ___|| | _(_)_ __ / ____|    | | | __ )| ____|
 \___ \| |/ / | '_ \ |  _ _____| |_|  _ \|  _|  
  ___) |   <| | | | | |_| |___|  _| |_) | |___
 |____/|_|\_\_|_| |_|\____|    |_| |____/|_____|
              Python 実装版 (研究・技術検証用)
    """
    print(banner)
    print("  Minecraft Bedrock Edition プレイヤースキン取得ツール")
    print("  ─────────────────────────────────────────────────")
    print("  注意: 本ツールは研究目的専用です。")
    print("        商用利用・公開サーバーでの使用は推奨されません。\n")


def main():
    print_banner()

    parser = argparse.ArgumentParser(description="SkinGetBE - Bedrock スキン取得サーバー")
    parser.add_argument("--config",       default="config.json", help="設定ファイルパス")
    parser.add_argument("--host",         help="バインドアドレス (デフォルト: 0.0.0.0)")
    parser.add_argument("--port",         type=int, help="ポート番号 (デフォルト: 19132)")
    parser.add_argument("--output",       help="スキン保存ディレクトリ (デフォルト: skins)")
    parser.add_argument("--protocol",     type=int, help="Bedrock プロトコルバージョン (デフォルト: 776)")
    parser.add_argument("--game-version", dest="game_version", help="ゲームバージョン文字列 (デフォルト: 1.21.0)")
    parser.add_argument("--debug",        action="store_true", help="デバッグログを有効化")
    args = parser.parse_args()

    # 設定読み込み
    config = load_config(args.config)

    # コマンドライン引数で上書き
    if args.host:         config["host"]         = args.host
    if args.port:         config["port"]         = args.port
    if args.output:       config["output_dir"]   = args.output
    if args.protocol:     config["protocol"]     = args.protocol
    if args.game_version: config["game_version"] = args.game_version
    if args.debug:        config["debug"]        = True

    # グローバルデバッグフラグ設定
    Logger.set_debug(config.get("debug", False))

    host         = config["host"]
    port         = config["port"]
    output_dir   = config["output_dir"]
    protocol     = config["protocol"]
    game_version = config["game_version"]

    # 出力ディレクトリ作成
    os.makedirs(output_dir, exist_ok=True)
    log.info(f"スキン保存先: {os.path.abspath(output_dir)}")

    # Pillow チェック
    try:
        import PIL
        log.ok("Pillow 検出: PNG 保存が可能です")
    except ImportError:
        log.warn("Pillow が未インストールです。スキンは .rgba ファイルとして保存されます")
        log.warn("インストール: pip install Pillow")

    # RakNet サーバー起動
    from raknet.raknet import RakNetServer

    server_guid = random.getrandbits(64)

    def callback(skin_data, addr):
        on_skin_received(skin_data, addr, output_dir)

    server = RakNetServer(
        host=host,
        port=port,
        server_guid=server_guid,
        on_skin_received=callback,
        protocol=protocol,
        game_version=game_version,
    )

    log.info(f"サーバー設定: {host}:{port}  protocol={protocol}  version={game_version}")
    log.info("Minecraft BE クライアントから以下のアドレスに接続してください:")
    log.info(f"  サーバーアドレス: <このPCのIPアドレス>")
    log.info(f"  ポート: {port}")
    log.info("接続するとスキンが自動的に保存されます。")
    log.info("Ctrl+C で停止\n")

    try:
        server.start()
    except PermissionError:
        log.error(f"ポート {port} のバインドに失敗しました。管理者権限が必要な場合があります。")
        log.error("別のポートを --port オプションで指定してください。")
        sys.exit(1)


if __name__ == "__main__":
    main()
