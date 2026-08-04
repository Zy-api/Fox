import os
import time
import base64
import requests
from telethon.sync import TelegramClient
from telethon.sessions import StringSession

api_id = 2496
api_hash = '8da85b0d5bfe62527e5b244c209159c3'
phone = '+959695609829'

GITHUB_TOKEN = os.environ.get('GITHUB_TOKEN', '')
REPO = os.environ.get('GITHUB_REPOSITORY', '')
CODE_FILE = 'session_code.txt'

def get_code_from_github():
    """从 GitHub 仓库读取验证码文件"""
    if not GITHUB_TOKEN or not REPO:
        return None
    url = f'https://api.github.com/repos/{REPO}/contents/{CODE_FILE}'
    headers = {'Authorization': f'token {GITHUB_TOKEN}', 'Accept': 'application/vnd.github.v3+json'}
    try:
        r = requests.get(url, headers=headers, timeout=10)
        if r.status_code == 200:
            content = base64.b64decode(r.json()['content']).decode('utf-8').strip()
            if content and len(content) >= 4:
                return content
    except:
        pass
    return None

def set_code_to_github(code):
    """写入验证码（用于测试）"""
    pass  # 由外部写入

print('=' * 60)
print('Connecting to Telegram...')
client = TelegramClient(StringSession(), api_id, api_hash)
client.connect()
print('Connected!')
print()

print('Sending verification code...')
result = client.send_code_request(phone)
phone_code_hash = result.phone_code_hash
print('✅ Code sent to your Telegram!')
print()
print('⏳ Waiting for code... (max 5 minutes)')
print('   Please send the code to your assistant')
print('=' * 60)
print()

# 最多等待 5 分钟
code = None
for i in range(30):  # 30 * 10s = 300s = 5min
    time.sleep(10)
    code = get_code_from_github()
    if code:
        print(f'✅ Got code: {code}')
        break
    print(f'  Waiting... ({(i+1)*10}s)')

if not code:
    print('❌ Timeout! No code received in 5 minutes.')
    client.disconnect()
    exit(1)

print()
print('Logging in...')
try:
    client.sign_in(phone, code, phone_code_hash=phone_code_hash)
except Exception as e:
    if 'password' in str(e).lower():
        print('🔒 2FA needed, checking password file...')
        # 尝试从环境变量读密码
        password = os.environ.get('TWO_FA_PASSWORD', '')
        if not password:
            # 也可以从文件读
            pwd_url = f'https://api.github.com/repos/{REPO}/contents/session_pwd.txt'
            headers = {'Authorization': f'token {GITHUB_TOKEN}', 'Accept': 'application/vnd.github.v3+json'}
            try:
                r = requests.get(pwd_url, headers=headers, timeout=10)
                if r.status_code == 200:
                    password = base64.b64decode(r.json()['content']).decode('utf-8').strip()
            except:
                pass
        
        if password:
            print('Using 2FA password from file...')
            client.sign_in(password=password)
        else:
            print('❌ 2FA password not found. Please set session_pwd.txt file.')
            client.disconnect()
            exit(1)
    else:
        raise e

me = client.get_me()
session_str = client.session.save()

print()
print('=' * 60)
print(f'✅ Login OK! @{me.username}')
print('=' * 60)
print()
print('SESSION_START')
print(session_str)
print('SESSION_END')
print()

# 保存到文件
with open('session.txt', 'w') as f:
    f.write(session_str)
print('Saved to session.txt')

client.disconnect()
