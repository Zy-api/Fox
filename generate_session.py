import os
from telethon.sync import TelegramClient
from telethon.sessions import StringSession

api_id = 2496
api_hash = '8da85b0d5bfe62527e5b244c209159c3'
phone = '+959695609829'

action = os.environ.get('ACTION', 'send_code')

client = TelegramClient(StringSession(), api_id, api_hash)
client.connect()

if action == 'send_code':
    print('=' * 60)
    print('Sending verification code...')
    result = client.send_code_request(phone)
    print('Code sent to Telegram!')
    print()
    print('PHONE_CODE_HASH_START')
    print(result.phone_code_hash)
    print('PHONE_CODE_HASH_END')
    print('=' * 60)

elif action == 'login':
    code = os.environ.get('CODE', '')
    phone_code_hash = os.environ.get('PHONE_CODE_HASH', '')
    password = os.environ.get('PASSWORD', '')

    print('Logging in...')
    try:
        client.sign_in(phone, code, phone_code_hash=phone_code_hash)
    except Exception as e:
        if 'password' in str(e).lower() and password:
            print('2FA needed, verifying...')
            client.sign_in(password=password)
        elif 'password' in str(e).lower():
            print('ERROR: 2FA password needed, please fill password field')
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

client.disconnect()
