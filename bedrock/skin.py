"""
Skin.py - スキン・ケープデータの解析と PNG 保存
Mirrors: Bedrock/Skin.cpp

numpy を使用:
  - RGBA バイト列を np.frombuffer で受け取り、形状検証・チャンネル確認
  - アルファ値の統計 (np.any) で有効ピクセル確認
  - save_rgba_as_png での array→PIL 変換
"""
import os
import json
import time
import numpy as np
from crypto.base64_util import decode
from crypto.jwt_util import decode_payload
from util.logger import Logger

log = Logger("Skin")

KNOWN_SKIN_SIZES = {
    64 * 32 * 4:   (64, 32),
    64 * 64 * 4:   (64, 64),
    128 * 128 * 4: (128, 128),
    256 * 256 * 4: (256, 256),
    128 * 64 * 4:  (128, 64),
}


def _decode_rgba(b64_data: str) -> bytes:
    try:
        return decode(b64_data)
    except Exception as e:
        log.warn(f"Base64 デコード失敗: {e}")
        return b""


def _validate_rgba(rgba_bytes: bytes, width: int, height: int) -> bool:
    """
    numpy で RGBA データを検証する。
      - バイト数が width*height*4 に一致するか
      - アルファチャンネルに 0 以外の値が存在するか (完全透明でないか)
    """
    expected = width * height * 4
    if len(rgba_bytes) != expected:
        log.warn(f"RGBAサイズ不一致: {len(rgba_bytes)} bytes (期待: {expected})")
        return False

    arr = np.frombuffer(rgba_bytes, dtype=np.uint8)
    alpha_channel = arr[3::4]   # A チャンネルだけ抽出
    has_visible = np.any(alpha_channel > 0)
    if not has_visible:
        log.warn("スキンのアルファチャンネルが全て 0 (完全透明)")
    return True


def save_rgba_as_png(rgba_bytes: bytes, width: int, height: int, filepath: str) -> bool:
    """
    RGBA バイト列を PNG ファイルとして保存する。
    numpy array 経由で PIL Image に変換する。
    """
    if not rgba_bytes:
        log.warn("スキンデータが空です")
        return False

    try:
        from PIL import Image
        # numpy array (height, width, 4) に reshape してから PIL へ
        arr = np.frombuffer(rgba_bytes, dtype=np.uint8).reshape((height, width, 4))
        img = Image.fromarray(arr, mode="RGBA")
        os.makedirs(os.path.dirname(filepath) if os.path.dirname(filepath) else ".", exist_ok=True)
        img.save(filepath, "PNG")
        log.ok(f"PNG 保存完了: {filepath} ({width}x{height})")
        return True
    except ImportError:
        raw_path = filepath.replace(".png", ".rgba")
        os.makedirs(os.path.dirname(raw_path) if os.path.dirname(raw_path) else ".", exist_ok=True)
        with open(raw_path, "wb") as f:
            f.write(rgba_bytes)
        log.warn(f"Pillow 未インストール → 生 RGBA 保存: {raw_path}")
        log.warn("PNG 変換には: pip install Pillow")
        return True
    except Exception as e:
        log.error(f"PNG 保存失敗: {e}")
        return False


def _guess_size(rgba_bytes: bytes, declared_w: int, declared_h: int) -> tuple:
    if declared_w > 0 and declared_h > 0:
        return declared_w, declared_h

    size = len(rgba_bytes)
    if size in KNOWN_SKIN_SIZES:
        return KNOWN_SKIN_SIZES[size]

    # numpy で正方形判定
    pixels = size // 4
    side = int(np.sqrt(pixels))
    if side * side == pixels:
        return side, side

    log.warn(f"サイズ推測失敗 ({size} bytes) → 64x64 として扱います")
    return 64, 64


class SkinData:
    def __init__(self):
        self.skin_id: str = ""
        self.player_name: str = ""
        self.xuid: str = ""
        self.uuid: str = ""
        self.skin_rgba: bytes = b""
        self.skin_width: int = 0
        self.skin_height: int = 0
        self.cape_rgba: bytes = b""
        self.cape_width: int = 0
        self.cape_height: int = 0
        self.geometry_name: str = ""
        self.geometry_json: str = ""
        self.premium_skin: bool = False

    def __str__(self):
        return (
            f"Player: {self.player_name} | XUID: {self.xuid} | UUID: {self.uuid}\n"
            f"  SkinID: {self.skin_id[:40]}...\n"
            f"  Skin: {self.skin_width}x{self.skin_height} ({len(self.skin_rgba)} bytes)\n"
            f"  Cape: {self.cape_width}x{self.cape_height} ({len(self.cape_rgba)} bytes)\n"
            f"  Geometry: {self.geometry_name}"
        )


def extract_skin_from_jwt(skin_jwt: str, chain_data: list) -> SkinData:
    skin = SkinData()

    # ─ Chain JWT からプレイヤー情報を取得 (C++ 版の main.cpp 221-238 相当) ─
    for token in chain_data:
        try:
            payload = decode_payload(token)

            # 1) extraData 内を確認 (Mojang 署名付きトークン)
            extra = payload.get("extraData", {})
            if extra:
                name = extra.get("displayName", "")
                if name:
                    skin.player_name = name
                xuid = extra.get("XUID", "")
                if xuid:
                    skin.xuid = xuid
                identity = extra.get("identity", "")
                if identity:
                    skin.uuid = identity

            # 2) トップレベルのフィールドも確認 (C++ 版: xname → ThirdPartyName → displayName)
            if not skin.player_name:
                name = payload.get("xname", "")
                if not name:
                    name = payload.get("ThirdPartyName", "")
                if not name:
                    name = payload.get("displayName", "")
                if name:
                    skin.player_name = name

        except Exception as e:
            log.debug(f"Chain JWT スキップ: {e}")

    if skin.player_name:
        log.info(f"プレイヤー情報: {skin.player_name} (XUID: {skin.xuid})")

    # ─ Skin JWT のデコード ─
    try:
        payload = decode_payload(skin_jwt)
    except Exception as e:
        log.error(f"Skin JWT デコード失敗: {e}")
        return skin

    # 3) Skin JWT からもプレイヤー名をフォールバック取得 (C++ 版と同様)
    if not skin.player_name:
        name = payload.get("ThirdPartyName", "")
        if not name:
            name = payload.get("displayName", "")
        if name:
            skin.player_name = name
            log.info(f"プレイヤー名 (Skin JWT より): {skin.player_name}")

    skin.skin_id       = payload.get("SkinId", "unknown")
    skin.premium_skin  = payload.get("PremiumSkin", False)
    skin.geometry_name = payload.get("SkinGeometryName", "geometry.humanoid.custom")

    geo_raw = payload.get("SkinGeometry", "")
    if geo_raw:
        try:
            skin.geometry_json = decode(geo_raw).decode("utf-8", errors="replace")
        except Exception:
            skin.geometry_json = geo_raw

    skin_data_b64 = payload.get("SkinData", "")
    if skin_data_b64:
        skin.skin_rgba   = _decode_rgba(skin_data_b64)
        skin.skin_width  = payload.get("SkinImageWidth", 0)
        skin.skin_height = payload.get("SkinImageHeight", 0)
        skin.skin_width, skin.skin_height = _guess_size(
            skin.skin_rgba, skin.skin_width, skin.skin_height
        )
        _validate_rgba(skin.skin_rgba, skin.skin_width, skin.skin_height)
        log.skin(f"スキン RGBA: {skin.skin_width}x{skin.skin_height} ({len(skin.skin_rgba)} bytes)")

    cape_data_b64 = payload.get("CapeData", "")
    if cape_data_b64:
        skin.cape_rgba   = _decode_rgba(cape_data_b64)
        skin.cape_width  = payload.get("CapeImageWidth", 0)
        skin.cape_height = payload.get("CapeImageHeight", 0)
        skin.cape_width, skin.cape_height = _guess_size(
            skin.cape_rgba, skin.cape_width, skin.cape_height
        )
        log.skin(f"ケープ RGBA: {skin.cape_width}x{skin.cape_height} ({len(skin.cape_rgba)} bytes)")

    return skin


def save_skin(skin: SkinData, output_dir: str = "skins") -> list:
    saved = []
    safe_name = skin.player_name if skin.player_name else "unknown"
    safe_name = "".join(c for c in safe_name if c.isalnum() or c in "_- ").strip() or "unknown"
    timestamp = int(time.time())
    base = os.path.join(output_dir, f"{safe_name}_{timestamp}")
    os.makedirs(output_dir, exist_ok=True)

    if skin.skin_rgba:
        path = f"{base}_skin.png"
        if save_rgba_as_png(skin.skin_rgba, skin.skin_width, skin.skin_height, path):
            saved.append(path)

    if skin.cape_rgba:
        path = f"{base}_cape.png"
        if save_rgba_as_png(skin.cape_rgba, skin.cape_width, skin.cape_height, path):
            saved.append(path)

    if skin.geometry_json:
        path = f"{base}_geometry.json"
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(json.loads(skin.geometry_json), f, indent=2, ensure_ascii=False)
        except Exception:
            with open(path, "w", encoding="utf-8") as f:
                f.write(skin.geometry_json)
        log.ok(f"Geometry JSON 保存: {path}")
        saved.append(path)

    path = f"{base}_info.json"
    info = {
        "playerName":   skin.player_name,
        "xuid":         skin.xuid,
        "uuid":         skin.uuid,
        "skinId":       skin.skin_id,
        "premiumSkin":  skin.premium_skin,
        "geometryName": skin.geometry_name,
        "skinSize":  {"width": skin.skin_width,  "height": skin.skin_height},
        "capeSize":  {"width": skin.cape_width,   "height": skin.cape_height},
        "savedAt":   timestamp,
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(info, f, indent=2, ensure_ascii=False)
    log.ok(f"プレイヤー情報保存: {path}")
    saved.append(path)

    return saved