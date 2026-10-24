"""Check the OneMap login and search step by step. Never prints the password or token.

    .\\.venv\\Scripts\\python.exe scripts\\onemap_check.py
"""
import json
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from app.stage2a.auth import env_values  # noqa: E402

BASE = 'https://www.onemap.gov.sg'


def call(url, data=None, headers=None):
    req = Request(url, data=data, headers=headers or {}, method='POST' if data else 'GET')
    try:
        with urlopen(req, timeout=20) as r:
            return r.status, r.read(4000).decode('utf-8', 'replace')
    except HTTPError as e:
        return e.code, e.read(1000).decode('utf-8', 'replace')
    except URLError as e:
        return None, f'network error: {e.reason}'


v = env_values(ROOT)
email = v.get('ONEMAP_EMAIL', '')
pw = v.get('ONEMAP_PASSWORD', '')
print('1) .env')
print(f'   ONEMAP_EMAIL: {"set" if email else "EMPTY"}'
      + (f' ({email[:2]}***@{email.split("@")[-1]})' if '@' in email else ''))
print(f'   ONEMAP_PASSWORD: {"set, " + str(len(pw)) + " characters" if pw else "EMPTY"}')
for label, val in (('email', email), ('password', pw)):
    if val != val.strip():
        print(f'   WARNING: {label} has spaces at the start or end')
    if val[:1] in '"\'' or val[-1:] in '"\'':
        print(f'   WARNING: {label} starts or ends with a quote mark; remove the quotes')
if v.get('ONEMAP_ACCESS_TOKEN'):
    print('   NOTE: ONEMAP_ACCESS_TOKEN is also set; the app uses it first. Clear it if it is old.')

print('2) Login (getToken)')
status, body = call(BASE + '/api/auth/post/getToken',
                    json.dumps({'email': email, 'password': pw}).encode(),
                    {'Content-Type': 'application/json', 'Accept': 'application/json'})
token = None
try:
    data = json.loads(body)
    token = data.get('access_token')
    shown = {k: (v2 if k not in ('access_token',) else '***') for k, v2 in data.items()}
except ValueError:
    shown = body[:300]
print(f'   HTTP {status}: {shown}')
if not token:
    sys.exit('   Login failed. Check the email/password, and that the account was confirmed by email.')

print('3) Search "Singapore Zoo"')
q = urlencode({'searchVal': 'Singapore Zoo', 'returnGeom': 'Y', 'getAddrDetails': 'Y', 'pageNum': 1})
status, body = call(f'{BASE}/api/common/elastic/search?{q}', headers={'Authorization': token, 'Accept': 'application/json'})
print(f'   HTTP {status}: {body[:400]}')
