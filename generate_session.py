import os
import time
import urllib.request
import urllib.error
from telethon.sync import TelegramClient
from telethon.sessions import StringSession

api_id = 2496
api_hash = '8da85b0d5bfe62527e5b244c209159c3'
phone = '+959695609829'

CODE_URL = 'https://raw.githubusercontent.com/Zy-api/Fox/main/session_code.txt'
PWD_URL = 'https://raw.githubusercontent.com/Zy-api/Fox/main/session_pwd.txt'

def get_code():
    try:
        req = urllib.request.Request(CODE_URL, headers={'Cache-Control': 'no-cache', 'Pragma': 'no-cache'})
        with urllib.request.urlopen(req, timeout=10) as resp:
            content = resp.read().decode('utf-8').strip()
            if content and len(content) >= 4 and content.isdigit():
                return content
    except:
        pass
    return None

def get_password():
    try:
        req = urllib.request.Request(PWD_URL, headers={'Cache-Control': 'no-cache'})
        with urllib.request.urlopen(req, timeout=10) as resp:
            content = resp.read().decode('utf-8').strip()
            if content:
                return content
    except:
        pass
    return None

print('=' * 60)
print('Connecting to Telegram...')
client = TelegramClient(StringSession(), api_id, api_hash)
client.connect()
print('Connected!')
print()

# 先测试一下能不能读到文件
test_code = get_code()
print(f'Test read file: {test_code if test_code else "(empty)"}')
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
    code = get_code()
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
        print('2FA needed, checking password...')
        password = get_password()
        if password:
            print('Using 2FA password...')
            client.sign_in(password=password)
        else:
            print('ERROR: 2FA password not found in session_pwd.txt')
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
