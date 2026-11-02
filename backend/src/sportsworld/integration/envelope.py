"""NetSecKit 信封加密（iOS 7.3.40 请求链）。

- keyDataOne/Two/Three 为内置静态值，keyDataFour = "%f" 毫秒末 12 字符（会话内缓存复用）
- inner container 固定键序 data,timeStamp,platform,keyDataOne..Four
- d=AES-128-CBC/PKCS7/IV=0(reqKey)；h=MD5(container)；k=RSA-1024 PKCS1v15(reqKey)
- outer 键序：observed=[k,p,d,h,t]（headerSign 用）；insert=[d,h,k,p,t]（body 用）
"""

import base64
import hashlib
import json
import secrets
import time

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import padding as rsa_padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

KEYDATA_ONE = "nhang.school"
KEYDATA_TWO = "5K0E8400-E29"
KEYDATA_THREE = "597DEA1AFB49"

# native reqKey 白名单字符池
SALT_ALPHABET = "+kot8A*B45jF6CD@a!UVWubcdKLZ{efgMpNOxyz01PQ}Rn)Tvw23XYh(iG7rsEqJHI9+Slm/"

# 客户端内置 RSA-1024 公钥（SPKI PEM）
RSA_PUB_PEM = b"""-----BEGIN PUBLIC KEY-----
MIGfMA0GCSqGSIb3DQEBAQUAA4GNADCBiQKBgQC5pqlTzsGNZk1RxhH4O4x3JNTD
V7FbVH66mPfW5v1tnIy4ty7xv8DGMG4Zn/TvstwlJWeYOADHdi8uF21lJaBvzvPt
VEhifHXZq825fI9hGYtDoaVQmCN/Nfs2dKmt89XDrhtl3SZxO6TumOCTQt+5oqjF
2Jo3o1YtkAyGzjaJnwIDAQAB
-----END PUBLIC KEY-----"""

RSA_PUB = serialization.load_pem_public_key(RSA_PUB_PEM)


def now_ms() -> int:
    return int(time.time() * 1000)


def md5_hex(data: bytes) -> str:
    return hashlib.md5(data).hexdigest()


def b64e(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def b64d(s: str) -> bytes:
    return base64.b64decode(s)


def aes128_cbc_encrypt(key: bytes, plaintext: bytes) -> bytes:
    pad = 16 - len(plaintext) % 16
    plaintext += bytes([pad]) * pad
    enc = Cipher(algorithms.AES128(key), modes.CBC(b"\x00" * 16)).encryptor()
    return enc.update(plaintext) + enc.finalize()


def aes128_cbc_decrypt(key: bytes, ciphertext: bytes) -> bytes:
    dec = Cipher(algorithms.AES128(key), modes.CBC(b"\x00" * 16)).decryptor()
    pt = dec.update(ciphertext) + dec.finalize()
    return pt[: -pt[-1]]


def req_key() -> str:
    return "".join(secrets.choice(SALT_ALPHABET) for _ in range(16))


def compact(obj: dict) -> str:
    return json.dumps(obj, separators=(",", ":"), ensure_ascii=False)


class EnvelopeSession:
    """keyDataFour 首次生成后会话内复用（native singleton 语义）。"""

    def __init__(self) -> None:
        self._four: str | None = None

    def key_data(self) -> list[str]:
        if self._four is None:
            # native "%f"：毫秒 double 取 6 位小数，末 12 字符
            self._four = f"{float(now_ms()):.6f}"[-12:]
        return [KEYDATA_ONE, KEYDATA_TWO, KEYDATA_THREE, self._four]


def build_container(data_b64: str, ts_ms: int, kd: list[str]) -> bytes:
    return compact({
        "data": data_b64,
        "timeStamp": ts_ms,
        "platform": 1,
        "keyDataOne": kd[0],
        "keyDataTwo": kd[1],
        "keyDataThree": kd[2],
        "keyDataFour": kd[3],
    }).encode("utf-8")


def build_envelope(session: EnvelopeSession, plain: str, order: str, ts_ms: int) -> tuple[str, list[str], int]:
    """order: "observed" -> k,p,d,h,t（headerSign）；"insert" -> d,h,k,p,t（body）。"""
    kd = session.key_data()
    container = build_container(b64e(plain.encode("utf-8")), ts_ms, kd)
    rk = req_key()
    d = b64e(aes128_cbc_encrypt(rk.encode("utf-8"), container))
    h = md5_hex(container)
    k = b64e(RSA_PUB.encrypt(rk.encode("utf-8"), rsa_padding.PKCS1v15()))
    if order == "observed":
        env = f'{{"k":"{k}","p":101,"d":"{d}","h":"{h}","t":0}}'
    else:
        env = f'{{"d":"{d}","h":"{h}","k":"{k}","p":101,"t":0}}'
    return env, kd, ts_ms
