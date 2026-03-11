"""
JWT.py - 署名検証なしの JWT デコード
Mirrors: Crypto/JWT.cpp

注意: 本実装は署名検証を行いません（研究・検証目的）
"""
import json
from crypto.base64_util import decode_url_safe


def decode_payload(token: str) -> dict:
    """
    JWT の Payload 部分を Base64 デコードして dict で返す。
    署名検証は行わない。

    Args:
        token: JWT 文字列 (header.payload.signature)

    Returns:
        デコードされた payload dict
    """
    parts = token.split(".")
    if len(parts) < 2:
        raise ValueError(f"不正な JWT フォーマット: {token[:40]}")

    payload_bytes = decode_url_safe(parts[1])
    return json.loads(payload_bytes.decode("utf-8"))


def decode_header(token: str) -> dict:
    """JWT の Header を Base64 デコードして返す"""
    parts = token.split(".")
    if not parts:
        raise ValueError("不正な JWT フォーマット")
    header_bytes = decode_url_safe(parts[0])
    return json.loads(header_bytes.decode("utf-8"))


def extract_chain(chain_json: str) -> list:
    """
    Login Packet の chain data JSON から JWT リストを取得する。

    Args:
        chain_json: {"chain": ["jwt1", "jwt2", ...]} 形式の JSON 文字列

    Returns:
        JWT トークンのリスト
    """
    data = json.loads(chain_json)
    return data.get("chain", [])
