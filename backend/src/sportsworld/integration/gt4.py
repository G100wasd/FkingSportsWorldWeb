"""GT4 滑块验证。

流程：GET /load（bg/slice/lot_number/payload/process_token/pow_detail）
  -> 下载两图 -> 缺口识别（Canny + 模板匹配）-> PoW（sha256 前导 00）
  -> get_w（AES-CBC 明文 + RSA 加密 str16 拼接）-> GET /verify -> 四凭证
"""

import hashlib
import io
import json
import secrets
import time
import uuid
from urllib.parse import quote

import httpx
import numpy as np
from PIL import Image
from cryptography.hazmat.primitives.asymmetric import padding as rsa_padding
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicNumbers
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from .envelope import now_ms

GEE_HOST = "https://gcaptcha4.geetest.com"
STATIC_HOST = "https://static.geetest.com/"
UA_WEB = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
          "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36")


def _gee_rsa_public_key():
    """jsbn 28bit limb 数组 -> RSA 公钥（e=65537）。"""
    limbs = [
        134982529, 254232810, 164556709, 234907349, 134685994, 35463984, 258277946, 12518857,
        44638621, 93783641, 212253739, 62792472, 186688352, 109500232, 182488077, 261196188,
        26354094, 103248217, 106891695, 165771045, 41530993, 263704736, 111785174, 12753611,
        232116673, 155524985, 218291229, 122452343, 248250238, 118739550, 251169095, 129059733,
        149835464, 5498868, 71719731, 154456417, 49635,
    ]
    dv = 1 << 28
    n = 0
    for limb in reversed(limbs):
        n = n * dv + limb
    return RSAPublicNumbers(65537, n).public_key()


def _four_random_chart() -> str:
    v = int(65536.0 * (1.0 + secrets.randbelow(1 << 53) / (1 << 53)))
    return format(v, "x")[1:5]


def _get_str_16() -> str:
    return "".join(_four_random_chart() for _ in range(4))


def _pow_msg(pow_detail: dict, captcha_id: str, lot_number: str) -> str:
    base = "|".join([
        str(pow_detail.get("version", "")),
        str(pow_detail.get("bits", "")),
        str(pow_detail.get("hashfunc", "")),
        str(pow_detail.get("datetime", "")),
        captcha_id,
        lot_number,
        "",
    ])
    attempts = 0
    while True:
        nonce = "".join(secrets.choice("0123456789abcdef") for _ in range(16))
        attempts += 1
        if hashlib.sha256((base + nonce).encode()).hexdigest().startswith("00"):
            print(f"[gt4] PoW 找到 nonce（尝试 {attempts} 次）")
            return base + nonce


def _aes_o(plaintext: str, str16: str) -> str:
    pt = plaintext.encode()
    pad = 16 - len(pt) % 16
    pt += bytes([pad]) * pad
    enc = Cipher(algorithms.AES128(str16.encode()), modes.CBC(b"0" * 16)).encryptor()
    return (enc.update(pt) + enc.finalize()).hex()


def _get_w(set_left: int, lot_number: str, pow_msg: str, pow_sign: str, str16: str) -> str:
    pk = _gee_rsa_public_key()
    userresponse = set_left / 1.0059466666666665 + 2.0
    lf = lambda a, b: lot_number[a:b]
    plaintext = (
        f'{{"setLeft":{set_left},"passtime":1887,"userresponse":{userresponse},'
        f'"device_id":"","lot_number":"{lot_number}","pow_msg":"{pow_msg}",'
        f'"pow_sign":"{pow_sign}","geetest":"captcha","lang":"zh","ep":"123",'
        f'"biht":"1426265548","gee_guard":{{"roe":{{"aup":"3","sep":"3","egp":"3",'
        f'"auh":"3","rew":"3","snh":"3","res":"3","cdc":"3"}}}},"YciC":"P3Vn",'
        f'"{lf(26, 30) + lf(7, 11)}":"{lf(6, 14)}",'
        f'"em":{{"ph":0,"cp":0,"ek":"11","wd":1,"nt":0,"si":0,"sc":0}}}}'
    )
    r = pk.encrypt(str16.encode(), rsa_padding.PKCS1v15()).hex()
    return _aes_o(plaintext, str16) + r


def _jsonp_value(text: str) -> dict:
    start = text.index("(")
    end = text.rindex(")")
    return json.loads(text[start + 1 : end])


# --- 缺口识别图像管线（numpy 版，对照 Rust gt4image） ---

def _decode_gray(png: bytes) -> np.ndarray:
    rgba = np.asarray(Image.open(io.BytesIO(png)).convert("RGBA"), dtype=np.uint32)
    a = rgba[..., 3]
    r = rgba[..., 0] * a // 255
    g = rgba[..., 1] * a // 255
    b = rgba[..., 2] * a // 255
    return ((r * 299 + g * 587 + b * 114) // 1000).astype(np.uint8)


def _gaussian_blur(src: np.ndarray) -> np.ndarray:
    sigma = 1.1
    k = np.exp(-((np.arange(5) - 2.0) ** 2) / (2 * sigma * sigma))
    k /= k.sum()
    img = src.astype(np.float32)
    tmp = np.zeros_like(img)
    for i in range(5):
        off = i - 2
        tmp += k[i] * np.pad(img, ((0, 0), (2, 2)), mode="edge")[:, off + 2 : off + 2 + img.shape[1]]
    out = np.zeros_like(tmp)
    for i in range(5):
        off = i - 2
        out += k[i] * np.pad(tmp, ((2, 2), (0, 0)), mode="edge")[off + 2 : off + 2 + tmp.shape[0], :]
    return out


def _canny(src: np.ndarray, low: float, high: float) -> np.ndarray:
    h, w = src.shape
    p = np.pad(src, 1, mode="edge")
    gx = (-p[:-2, :-2] - 2 * p[1:-1, :-2] - p[2:, :-2]
          + p[:-2, 2:] + 2 * p[1:-1, 2:] + p[2:, 2:])
    gy = (-p[:-2, :-2] - 2 * p[:-2, 1:-1] - p[:-2, 2:]
          + p[2:, :-2] + 2 * p[2:, 1:-1] + p[2:, 2:])
    mag = np.hypot(gx, gy)
    ang = np.arctan2(gy, gx)
    ang[ang < 0] += np.pi
    q = np.zeros_like(ang, dtype=np.int8)
    q[ang < np.pi / 4] = 0
    q[(ang >= np.pi / 4) & (ang < np.pi / 2)] = 1
    q[(ang >= np.pi / 2) & (ang < 3 * np.pi / 4)] = 2
    q[ang >= 3 * np.pi / 4] = 3
    nms = np.zeros_like(mag)
    m = mag[1:-1, 1:-1]
    qi = q[1:-1, 1:-1]
    pairs = [(1, 0), (1, 1), (0, 1), (-1, 1)]
    keep = np.zeros_like(m, dtype=bool)
    for qi_v, (dx, dy) in enumerate(pairs):
        m1 = mag[1 + dy : h - 1 + dy, 1 + dx : w - 1 + dx]
        m2 = mag[1 - dy : h - 1 - dy, 1 - dx : w - 1 - dx]
        keep |= (qi == qi_v) & (m >= m1) & (m >= m2)
    nms[1:-1, 1:-1] = np.where(keep, m, 0)
    out = (nms >= high).astype(np.uint8) * 255
    weak = nms >= low
    prev = np.zeros_like(out)
    while not np.array_equal(out, prev):
        prev = out
        padded = np.pad(out, 1)
        grown = np.zeros_like(out, dtype=bool)
        for dy in range(3):
            for dx in range(3):
                grown |= padded[dy : dy + h, dx : dx + w] > 0
        out = np.where(weak & grown, 255, out).astype(np.uint8)
    return out


def _match_x(bg: np.ndarray, tpl: np.ndarray) -> int:
    """TM_CCOEFF_NORMED 等价：零均值模板相关 / 窗口能量，返回最佳 x。"""
    bh, bw = bg.shape
    th, tw = tpl.shape
    if tw == 0 or th == 0 or tw > bw or th > bh:
        return 0
    t = tpl.astype(np.float64) - tpl.mean()
    t_norm = np.sqrt((t * t).sum())
    if t_norm == 0:
        return 0
    n = tw * th
    i1 = np.pad(bg.astype(np.float64), ((1, 0), (1, 0)), mode="constant").cumsum(0).cumsum(1)
    i2 = np.pad((bg.astype(np.float64)) ** 2, ((1, 0), (1, 0)), mode="constant").cumsum(0).cumsum(1)

    best_score, best_x = -np.inf, 0
    for oy in range(bh - th + 1):
        win = np.lib.stride_tricks.sliding_window_view(bg[oy : oy + th], (tw,), axis=1)
        num = np.tensordot(win.astype(np.float64), t, axes=([0, 2], [0, 1]))
        sums = i1[oy + th, tw : bw + 1] - i1[oy, tw : bw + 1] - i1[oy + th, : bw - tw + 1] + i1[oy, : bw - tw + 1]
        sqs = i2[oy + th, tw : bw + 1] - i2[oy, tw : bw + 1] - i2[oy + th, : bw - tw + 1] + i2[oy, : bw - tw + 1]
        energy = np.sqrt(np.maximum(sqs - sums * sums / n, 0.0)) * t_norm
        with np.errstate(invalid="ignore", divide="ignore"):
            score = np.where(energy == 0, 0.0, num / energy)
        ox = int(np.argmax(score))
        if score[ox] > best_score:
            best_score, best_x = score[ox], ox
    return best_x


def get_distance_original(bg_png: bytes, slice_png: bytes) -> int:
    bg = _canny(_gaussian_blur(_decode_gray(bg_png)), 100.0, 200.0)
    sl = _canny(_gaussian_blur(_decode_gray(slice_png)), 100.0, 200.0)
    if int((sl == 255).sum()) < 10:
        raise RuntimeError("切片图无边缘特征（识别失败）")
    return _match_x(bg, sl)


# --- GT4 求解 ---

def _solve_gt4_once(captcha_id: str, http: httpx.Client) -> dict:
    challenge = uuid.uuid4().hex
    callback = f"geetest_{now_ms()}"
    load_url = (f"{GEE_HOST}/load?callback={callback}&captcha_id={captcha_id}"
                f"&challenge={challenge}&client_type=web&risk_type=slide&lang=zh")
    text = http.get(load_url, headers={"User-Agent": UA_WEB}).text
    data = _jsonp_value(text)["data"]
    bg_png = http.get(STATIC_HOST + data["bg"], headers={"User-Agent": UA_WEB}).content
    slice_png = http.get(STATIC_HOST + data["slice"], headers={"User-Agent": UA_WEB}).content
    lot_number = data["lot_number"]
    dist = get_distance_original(bg_png, slice_png)
    print(f"[gt4] 缺口识别 distance={dist}")

    pow_msg = _pow_msg(data["pow_detail"], captcha_id, lot_number)
    pow_sign = hashlib.sha256(pow_msg.encode()).hexdigest()
    str16 = _get_str_16()
    w = _get_w(dist, lot_number, pow_msg, pow_sign, str16)

    cb = f"geetest_{now_ms()}"
    verify_url = (
        f"{GEE_HOST}/verify?callback={cb}&captcha_id={captcha_id}&client_type=web"
        f"&lot_number={quote(lot_number)}&risk_type=slide"
        f"&payload={quote(data['payload'])}&process_token={quote(data['process_token'])}"
        f"&payload_protocol=1&pt=1&w={quote(w)}"
    )
    text = http.get(verify_url, headers={"User-Agent": UA_WEB}).text
    root = _jsonp_value(text)
    if root.get("status") != "success":
        raise RuntimeError(f"GT4 /verify 失败: {text[:200]}")
    sec = root["data"]["seccode"]
    return {
        "lotNumber": sec["lot_number"],
        "captchaOutput": sec["captcha_output"],
        "passToken": sec["pass_token"],
        "genTime": sec["gen_time"],
    }


def solve_gt4(captcha_id: str) -> dict:
    """完整 GT4 求解（带重试）。返回 {lotNumber, captchaOutput, passToken, genTime}。"""
    last_err = ""
    with httpx.Client(timeout=30.0) as http:
        for attempt in range(1, 4):
            try:
                return _solve_gt4_once(captcha_id, http)
            except Exception as e:
                print(f"x [gt4] 第 {attempt} 次失败: {e}")
                last_err = str(e)
                time.sleep(3)
    raise RuntimeError(f"GT4 连续 3 次失败: {last_err}")
