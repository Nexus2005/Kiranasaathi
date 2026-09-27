import hashlib
import binascii
from pathlib import Path

salt = "kiranaSaathiSalt01"
dk = hashlib.pbkdf2_hmac("sha256", b"Demo@12345", salt.encode(), 310000)
h = "pbkdf2_sha256$310000$" + salt + "$" + binascii.hexlify(dk).decode()
Path("database/seed/demo_password_hash.txt").write_text(h, encoding="utf-8")
print(h)
