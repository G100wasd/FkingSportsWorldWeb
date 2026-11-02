"""临时自检：用 Rust 测试向量验证移植正确性。验证后可删除。"""
import json

from sportsworld.integration.envelope import (
    EnvelopeSession, aes128_cbc_decrypt, aes128_cbc_encrypt,
    build_container, build_envelope, md5_hex,
)
from sportsworld.integration.decrypt import derive_paes_key, fold32
from sportsworld.integration.header import token_sign

assert hex(fold32("nhang.school")) == "0x61297d61"
assert hex(fold32("5K0E8400-E29")) == "0x203a324c"
assert hex(fold32("597DEA1AFB49")) == "0x363a323c"
assert hex(fold32("86123.456789")) == "0x3d2f3d3e"

k = derive_paes_key("nhang.school", "5K0E8400-E29", "597DEA1AFB49", "86123.456789")
assert k.hex() == "faaed5a4d99af386a7d3f5d109071f13", k.hex()

assert token_sign(13056447, "TOKENABC", 1788958186123) == "4a8d163186d91ac7b539a7bb07d457ba"
assert md5_hex(b"abc") == "900150983cd24fb0d6963f7d28e17f72"

ct = aes128_cbc_encrypt(b"0123456789abcdef", b"hello world")
assert aes128_cbc_decrypt(b"0123456789abcdef", ct) == b"hello world"

s = EnvelopeSession()
env, kd, ts = build_envelope(s, '{"a":1}', "insert", 1788958186123)
o = json.loads(env)
assert list(o.keys()) == ["d", "h", "k", "p", "t"] and o["p"] == 101 and o["t"] == 0

env2, _, _ = build_envelope(s, "x", "observed", 1788958186123)
assert list(json.loads(env2).keys()) == ["k", "p", "d", "h", "t"]

assert len(s.key_data()[3]) == 12 and s.key_data()[3] == s.key_data()[3]

c = build_container("AAAA", 1788958186123, kd)
assert list(json.loads(c).keys()) == ["data", "timeStamp", "platform", "keyDataOne", "keyDataTwo", "keyDataThree", "keyDataFour"]

print("all vectors OK")
