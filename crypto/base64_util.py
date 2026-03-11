"""
Base64.py - Base64 エンコード/デコードユーティリティ
Mirrors: Crypto/Base64.cpp
"""
import base64


def decode(data: str) -> bytes:
    """パディングを補完して Base64 デコード"""
    # URL-safe / 通常 Base64 両方に対応
    data = data.replace("-", "+").replace("_", "/")
    padding = 4 - len(data) % 4
    if padding != 4:
        data += "=" * padding
    return base64.b64decode(data)


def encode(data: bytes) -> str:
    """標準 Base64 エンコード"""
    return base64.b64encode(data).decode("ascii")


def decode_url_safe(data: str) -> bytes:
    """URL-safe Base64 デコード (パディング補完込み)"""
    padding = 4 - len(data) % 4
    if padding != 4:
        data += "=" * padding
    return base64.urlsafe_b64decode(data)
