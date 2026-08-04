import os
import time
import base64
import json
import urllib.request
import urllib.error
from telethon.sync import TelegramClient
from telethon.sessions import StringSession

api_id = 2496
api_hash = '8da85b0d5bfe62527e5b244c209159c3'
phone = '+959695609829'

GITHUB_TOKEN = os.environ.get('GITHUB_TOKEN', '')
REPO = os.environ.get('GITHUB_REPOSITORY', '')
CODE_FILE = 'session_code.txt'

def github_api(path, method='GET', data=None):
    url = f'https://api.github.com/repos/{REPO}/{path}'
    headers = {
        'Authorization': f'token {GITHUB_TOKEN}',
        'Accept': 'application/vnd.github.v3+json',
        'User-Agent': 'python-script'
    }
    if data:
        data_bytes = json.dumps(data).encode('utf-8')
        headers['Content-Type'] = 'application/json'
    else:
        data_bytes = None
    req = urllib.request.Request(url, data=data_bytes, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = resp.read().decode('utf-8')
            return resp.status, json.loads(body) if body else {}
    except urllib.error.HTTPError as e:
        body = e.read().decode('utf-8')
        return e.code, json.loads(body) if body else {}
    except Exception as e:
        return 0, {'error': str(e)}

def get_code_from_github():
    if not GITHUB_TOKEN or not REPO:
        return None
    status, data = github_api(f'contents/{CODE_FILE}?ref=main')
    if status == 200 and 'content' in data:
        content = base64.b64decode(data['content']).decode('utf-8').strip()
        if content and len(content) >= 4 and content.isdigit():
            return content
    return None

print('=' * 60)
print('Connecting to Telegram...')
client = TelegramClient(StringSession(), api_id, api_hash)
client.connect()
print('Connected!')
print()

print('Sending verification code...')
result = client.send_code_request(phone)
phone_code_hash = result.phone_code_hash
print('Code sent to your Telegram!')
print()
print('Waiting for code... (max 5 minutes)')
print('Please send the code to your assistant')
print('=' * 60)
print()

code = None
for i in range(30):
    time.sleep(10)
    code = get_code_from_github()
    if code:
        print(f'Got code: {code}')
        break
    print(f'  Waiting... ({(i+1)*10}s)')

if not code:
    print('Timeout! No code received in 5 minutes.')
    client.disconnect()
    exit(1)

print()
print('Logging in...')
try:
    client.sign_in(phone, code, phone_code_hash=phone_code_hash)
except Exception as e:
    if 'password' in str(e).lower():
        print('2FA needed...')
        # 尝试从文件读密码
        pwd_status, pwd_data = github_api('contents/session_pwd.txt?ref=main')
        password = ''
        if pwd_status == 200 and 'content' in pwd_data:
            password = base64.b64decode(pwd_data['content']).decode('utf-8').strip()
        
        if password:
            print('Using 2FA password...')
            client.sign_in(password=password)
        else:
            print('ERROR: 2FA password not found.')
            client.disconnect()
            exit(1)
    else:
        raise e

me = client.get_me()
session_str = client.session.save()

print()
print('=' * 60)
print(f'Login OK! @{me.username}')
print('=' * 60)
print()
print('SESSION_START')
print(session_str)
print('SESSION_END')
print()

with open('session.txt', 'w') as f:
    f.write(session_str)
print('Saved to session.txt')

client.disconnect()
