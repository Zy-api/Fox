#!/usr/bin/env python3
"""
Telegram 登录脚本 - 生成会话字符串
运行方式：python3 tg_login.py
"""
import os
import sys

print('=' * 50)
print('🔐 Telegram 登录工具 - 生成会话字符串')
print('=' * 50)
print()

# 安装 telethon
print('📦 正在安装依赖...')
import subprocess
result = subprocess.run([sys.executable, '-m', 'pip', 'install', 'telethon', '-q'], 
                       capture_output=True, text=True, timeout=120)
if result.returncode != 0:
    print(f'❌ 安装失败: {result.stderr}')
    sys.exit(1)
print('   ✅ 依赖安装完成')
print()

try:
    from telethon.sync import TelegramClient
    from telethon.sessions import StringSession
except ImportError:
    print('❌ Telethon 导入失败')
    sys.exit(1)

# 获取用户输入
print('📋 请输入以下信息：')
print()

api_id = input('   API ID: ').strip()
while not api_id or not api_id.isdigit():
    print('   ❌ 请输入有效的 API ID（纯数字）')
    api_id = input('   API ID: ').strip()

api_hash = input('   API Hash: ').strip()
while not api_hash:
    print('   ❌ API Hash 不能为空')
    api_hash = input('   API Hash: ').strip()

phone = input('   手机号（带国家代码，如 +8613800138000）: ').strip()
while not phone:
    print('   ❌ 手机号不能为空')
    phone = input('   手机号: ').strip()

print()
print('🔌 正在连接 Telegram...')

try:
    client = TelegramClient(StringSession(), int(api_id), api_hash)
    client.connect()
    
    if client.is_user_authorized():
        print('   ✅ 已经登录过了')
    else:
        print('📨 正在发送验证码...')
        client.send_code_request(phone)
        print()
        code = input('   请输入 Telegram 收到的验证码: ').strip()
        
        try:
            client.sign_in(phone, code)
        except Exception as e:
            if 'password' in str(e).lower() or 'SESSION_PASSWORD_NEEDED' in str(e):
                print()
                print('   🔒 检测到两步验证')
                password = input('   请输入二级密码: ').strip()
                client.sign_in(password=password)
            else:
                raise e
    
    print()
    me = client.get_me()
    print(f'✅ 登录成功！')
    print(f'   用户名: @{me.username}')
    print(f'   手机号: {me.phone}')
    print()
    
    session_string = client.session.save()
    
    print('=' * 50)
    print('📝 你的会话字符串：')
    print('=' * 50)
    print(session_string)
    print('=' * 50)
    print()
    print('⚠️  重要提示：')
    print('   1. 请把上面这一长串字符串完整复制保存')
    print('   2. 会话字符串相当于你的账号密码，不要泄露给他人')
    print('   3. 在 GitHub Secrets 中添加名为 TG_SESSION 的 Secret')
    print('   4. 还需要添加 TG_API_ID 和 TG_API_HASH 两个 Secret')
    print()
    
    client.disconnect()
    
except Exception as e:
    print()
    print(f'❌ 出错了: {e}')
    import traceback
    traceback.print_exc()
    sys.exit(1)
