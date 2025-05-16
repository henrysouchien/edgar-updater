#!/usr/bin/env python
# coding: utf-8

# In[ ]:


import json
import secrets
import string
from pathlib import Path

# === CONFIG ===
KEY_LENGTH = 16
KEYS_FILE = Path("valid_keys.json")

def generate_secure_key(length=KEY_LENGTH):
    alphabet = string.ascii_letters + string.digits
    return ''.join(secrets.choice(alphabet) for _ in range(length))

# === Load existing keys ===
if KEYS_FILE.exists():
    with open(KEYS_FILE, "r") as f:
        key_map = json.load(f)
else:
    key_map = {}

# === Input user info ===
user_email = input("Enter user email (used as key label): ").strip().lower()
if not user_email:
    print("❌ Email is required.")
    exit()

if user_email in key_map:
    print(f"⚠️ A key for '{user_email}' already exists.")
    print("Please use a different email or remove the existing one from valid_keys.json.")
    exit()

tier = input("Enter tier (registered or paid): ").strip().lower()
if tier not in ["registered", "paid"]:
    print("❌ Invalid tier. Use 'registered' or 'paid'")
    exit()

# === Generate key ===
new_key = generate_secure_key()

# === Save new key
key_map[user_email] = {
    "key": new_key,
    "tier": tier
}

# === Write to file
with open(KEYS_FILE, "w") as f:
    json.dump(key_map, f, indent=2)

# === Output key
print(f"\n✅ API key created for '{user_email}' [{tier}]:\n{new_key}")


# In[ ]:




