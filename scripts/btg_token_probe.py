"""Authorized one-off probe to find the correct OAuth token endpoint.

User explicitly authorized this in the chat session. Tries a small
curated list of plausible BTG hosts and standard OAuth paths, with a
short timeout. Prints status codes and short response previews only.
"""
from __future__ import annotations

import base64
import os
import sys

import requests

CLIENT_ID = os.environ["BTG_CLIENT_ID"]
CLIENT_SECRET = os.environ["BTG_CLIENT_SECRET"]
BASIC = base64.b64encode(f"{CLIENT_ID}:{CLIENT_SECRET}".encode()).decode()

HOSTS = [
    "api.btgpactual.com",
    "developer-partner.btgpactual.com",
    "api.developer-partner.btgpactual.com",
]
PATHS = [
    "/iaas-auth/api/v1/api/Token",
    "/iaas-auth/api/v1/Token",
    "/api/v1/authenticator",
    "/auth/token",
    "/v1/token",
    "/api/v1/auth",
]
SCOPES = ["apps", "openid", "default", ""]

headers = {
    "Authorization": f"Basic {BASIC}",
    "Content-Type": "application/x-www-form-urlencoded",
    "Accept": "application/json",
}

print(f"client_id (last 4): ...{CLIENT_ID[-4:]}", flush=True)

found = False
for host in HOSTS:
    for path in PATHS:
        url = f"https://{host}{path}"
        try:
            r = requests.post(
                url,
                headers=headers,
                data={"grant_type": "client_credentials", "scope": "apps"},
                timeout=6,
                allow_redirects=False,
            )
        except requests.RequestException as e:
            print(f"  ERR  {url} -> {type(e).__name__}: {str(e)[:100]}", flush=True)
            continue
        body = (r.text or "")[:220].replace("\n", " ")
        print(f"  [{r.status_code}] {url} -> {body}", flush=True)
        # 200 = success; 400/401 with structured JSON also tells us the
        # endpoint is real (just rejecting our request).
        if r.status_code == 200:
            print("\n*** SUCCESS — token endpoint:", url, flush=True)
            print(r.text)
            found = True
            break
    if found:
        break

if not found:
    print("\nNo 200 response. The 4xx codes with JSON bodies (if any) indicate live OAuth servers — share those lines.")
