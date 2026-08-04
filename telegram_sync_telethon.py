#!/usr/bin/env python3
"""
Telegram 频道自动同步脚本 (Telethon 版本)
使用用户账号登录，从公开频道下载文件并上传到 GitHub Release
"""
import os
import sys
import json
import re
import time
import base64
import urllib.request
import urllib.error
import urllib.parse
from datetime import datetime, timezone, timedelta

# ========== 配置 ==========
CHANNELS = [
    {'username': 'PNAyyds', 'keywords': ['PNA', 'PAN'], 'tag': '💜', 'icon': 'pna'},
    {'username': 'hhhhp', 'keywords': ['芒果', '客户端'], 'tag': '🥭', 'icon': 'mango'},
]

GITHUB_TOKEN = os.environ.get('GITHUB_TOKEN', '')
GITHUB_OWNER = 'Zy-api'
GITHUB_REPO = 'Fox'
GITHUB_BRANCH = 'main'
DATA_FILE = 'pan-data.json'
LOG_FILE = 'sync-log.txt'

# Telegram 配置（从环境变量读取）
# 使用 Telegram Web Z 官方公开 API 凭证
API_ID = int(os.environ.get('TG_API_ID', '2496'))
API_HASH = os.environ.get('TG_API_HASH', '8da85b0d5bfe62527e5b244c209159c3')
SESSION_STRING = os.environ.get('TG_SESSION', '')

CST = timezone(timedelta(hours=8))
log_lines = []


def log(msg):
    ts = datetime.now(CST).strftime('%Y-%m-%d %H:%M:%S')
    line = f'[{ts}] {msg}'
    print(line, flush=True)
    log_lines.append(line)


def save_log_to_github():
    if not GITHUB_TOKEN:
        return
    log_content = '\n'.join(log_lines)
    existing = None
    try:
        url = f'https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/contents/{LOG_FILE}?ref={GITHUB_BRANCH}'
        headers = {'Authorization': f'token {GITHUB_TOKEN}', 'Accept': 'application/vnd.github.v3+json'}
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=10) as resp:
            existing = json.loads(resp.read().decode('utf-8'))
    except:
        pass
    sha = existing.get('sha') if existing else None
    content_b64 = base64.b64encode(log_content.encode('utf-8')).decode('utf-8')
    payload = {'message': f'sync-log: {datetime.now(CST).strftime("%Y-%m-%d %H:%M")}', 'content': content_b64, 'branch': GITHUB_BRANCH}
    if sha:
        payload['sha'] = sha
    try:
        url = f'https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/contents/{LOG_FILE}'
        headers = {'Authorization': f'token {GITHUB_TOKEN}', 'Accept': 'application/vnd.github.v3+json', 'Content-Type': 'application/json'}
        req = urllib.request.Request(url, data=json.dumps(payload).encode('utf-8'), headers=headers, method='PUT')
        with urllib.request.urlopen(req, timeout=10) as resp:
            pass
    except Exception as e:
        print(f'保存日志失败: {e}')


def github_api_request(path, method='GET', data=None, content_type='application/json', raw_url=None):
    url = raw_url if raw_url else f'https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/{path}'
    headers = {'Authorization': f'token {GITHUB_TOKEN}', 'Accept': 'application/vnd.github.v3+json', 'User-Agent': 'SyncBot'}
    if content_type:
        headers['Content-Type'] = content_type
    body = None
    if data:
        body = data if isinstance(data, bytes) else json.dumps(data).encode('utf-8')
    try:
        req = urllib.request.Request(url, data=body, headers=headers, method=method)
        with urllib.request.urlopen(req, timeout=120) as resp:
            result = resp.read()
            return json.loads(result.decode('utf-8')) if result else {'status': 'success'}
    except urllib.error.HTTPError as e:
        error_body = e.read().decode('utf-8', errors='ignore')[:300]
        if e.code == 204:
            return {'status': 'success'}
        log(f'❌ GitHub API HTTP {e.code}: {error_body[:150]}')
        return None
    except Exception as e:
        log(f'❌ GitHub API 失败: {e}')
        return None


def load_remote_data():
    result = github_api_request(f'contents/{DATA_FILE}?ref={GITHUB_BRANCH}')
    if result and 'content' in result:
        try:
            content = base64.b64decode(result['content']).decode('utf-8')
            return json.loads(content), result.get('sha')
        except Exception as e:
            log(f'❌ 解析数据失败: {e}')
    return None, None


def save_remote_data(data, sha):
    content = json.dumps(data, indent=2, ensure_ascii=False)
    content_b64 = base64.b64encode(content.encode('utf-8')).decode('utf-8')
    payload = {'message': f'auto-sync: {datetime.now(CST).strftime("%Y-%m-%d %H:%M")}', 'content': content_b64, 'branch': GITHUB_BRANCH}
    if sha:
        payload['sha'] = sha
    result = github_api_request(f'contents/{DATA_FILE}', 'PUT', payload)
    return result is not None and 'content' in result


def get_or_create_release():
    release_tag = 'telegram-files'
    release = github_api_request(f'releases/tags/{release_tag}')
    if not release:
        log('📦 创建 Release...')
        release = github_api_request('releases', 'POST', {'tag_name': release_tag, 'name': '📦 资源文件库', 'body': '自动同步资源', 'draft': False, 'prerelease': False})
    return release


def upload_file_to_release(file_path, file_name, release):
    upload_url = release.get('upload_url', '').replace('{?name,label}', f'?name={urllib.parse.quote(file_name)}')
    if not upload_url:
        return None
    log(f'⬆️  上传: {file_name}')
    try:
        with open(file_path, 'rb') as f:
            file_data = f.read()
        result = github_api_request('', 'POST', file_data, 'application/octet-stream', upload_url)
        if result and 'browser_download_url' in result:
            log(f'   ✅ 上传成功 ({len(file_data)/1024/1024:.1f} MB)')
            return result['browser_download_url']
        return None
    except Exception as e:
        log(f'❌ 上传失败: {e}')
        return None


def delete_release_asset(asset_id):
    result = github_api_request(f'releases/assets/{asset_id}', 'DELETE')
    return result is not None


def match_keywords(filename, keywords):
    filename_lower = filename.lower()
    for kw in keywords:
        if kw.lower() in filename_lower:
            return True
    return False


def parse_size(size_str):
    """解析文件大小字符串为字节数"""
    if not size_str:
        return 0
    size_str = size_str.strip().upper()
    try:
        if 'GB' in size_str:
            return int(float(size_str.replace('GB', '').strip()) * 1024 * 1024 * 1024)
        elif 'MB' in size_str:
            return int(float(size_str.replace('MB', '').strip()) * 1024 * 1024)
        elif 'KB' in size_str:
            return int(float(size_str.replace('KB', '').strip()) * 1024)
        else:
            return int(float(size_str))
    except:
        return 0


def format_size(bytes_size):
    """格式化字节数为可读字符串"""
    if bytes_size >= 1024 * 1024 * 1024:
        return f'{bytes_size/1024/1024/1024:.1f} GB'
    elif bytes_size >= 1024 * 1024:
        return f'{bytes_size/1024/1024:.0f} MB'
    elif bytes_size >= 1024:
        return f'{bytes_size/1024:.0f} KB'
    else:
        return f'{bytes_size} B'


def sync_channel(client, channel_info, release, download_dir):
    """同步单个频道的最新文件"""
    username = channel_info['username']
    keywords = channel_info['keywords']
    tag = channel_info['tag']
    icon = channel_info['icon']
    
    log(f'📡 同步频道: @{username}')
    
    try:
        from telethon.tl.types import DocumentAttributeFilename, MessageMediaDocument
    except ImportError:
        log('   ❌ Telethon 未正确安装')
        return None
    
    try:
        # 获取频道实体
        channel = client.get_entity(username)
        log(f'   ✅ 找到频道: {channel.title}')
    except Exception as e:
        log(f'   ❌ 获取频道失败: {e}')
        return None
    
    # 遍历最新消息，找匹配的文件
    matching_files = []
    try:
        for message in client.iter_messages(channel, limit=100):
            if message.media and isinstance(message.media, MessageMediaDocument):
                doc = message.media.document
                # 获取文件名
                filename = None
                for attr in doc.attributes:
                    if isinstance(attr, DocumentAttributeFilename):
                        filename = attr.file_name
                        break
                
                if filename and match_keywords(filename, keywords):
                    matching_files.append({
                        'message': message,
                        'filename': filename,
                        'size': doc.size,
                        'date': message.date,
                        'id': message.id,
                    })
                    log(f'   📄 找到匹配: {filename} ({format_size(doc.size)})')
    except Exception as e:
        log(f'   ❌ 遍历消息失败: {e}')
        return None
    
    if not matching_files:
        log('   ⚠️  没有找到匹配的文件')
        return None
    
    # 取最新的一个
    latest = max(matching_files, key=lambda x: x['id'])
    log(f'   🎯 最新文件: {latest["filename"]}')
    
    # 下载文件
    safe_name = latest['filename'].replace(' ', '_')
    file_path = os.path.join(download_dir, f'{username}_{safe_name}')
    
    log(f'   ⬇️  下载中...')
    try:
        client.download_media(latest['message'], file=file_path)
        
        if os.path.exists(file_path):
            actual_size = os.path.getsize(file_path)
            log(f'   ✅ 下载完成 ({format_size(actual_size)})')
        else:
            log(f'   ❌ 下载失败，文件不存在')
            return None
    except Exception as e:
        log(f'   ❌ 下载异常: {e}')
        return None
    
    # 上传到 GitHub Release
    download_url = upload_file_to_release(file_path, latest['filename'], release)
    
    if download_url:
        date_str = latest['date'].strftime('%Y-%m-%d') if latest['date'] else datetime.now(CST).strftime('%Y-%m-%d')
        return {
            'name': latest['filename'],
            'size': format_size(latest['size']),
            'date': date_str,
            'direct_url': download_url,
            'url': f'https://t.me/{username}/{latest["id"]}',
            'tag': tag,
            'icon': icon,
            'channel': username,
            'desc': f'来自 @{username}',
            'msg_id': latest['id'],
        }
    
    return None


def main():
    log('=' * 50)
    log('🚀 Telegram 自动同步脚本 (Telethon 版本)')
    log('=' * 50)
    
    # 检查配置
    if not GITHUB_TOKEN:
        log('❌ 缺少 GITHUB_TOKEN')
        save_log_to_github()
        return 1
    
    if not API_ID or not API_HASH:
        log('❌ 缺少 TG_API_ID 或 TG_API_HASH')
        log('   请在 GitHub Secrets 中添加 TG_API_ID 和 TG_API_HASH')
        save_log_to_github()
        return 1
    
    if not SESSION_STRING:
        log('❌ 缺少 TG_SESSION')
        log('   请运行 login.py 生成会话字符串，然后添加到 GitHub Secrets')
        save_log_to_github()
        return 1
    
    # 安装依赖
    log('📦 安装依赖...')
    import subprocess
    result = subprocess.run([sys.executable, '-m', 'pip', 'install', 'telethon', '--quiet'], 
                          capture_output=True, text=True, timeout=120)
    if result.returncode != 0:
        log(f'   ❌ 安装失败: {result.stderr[:200]}')
        save_log_to_github()
        return 1
    log('   ✅ Telethon 安装完成')
    
    # 创建下载目录
    download_dir = '/tmp/telegram_downloads'
    os.makedirs(download_dir, exist_ok=True)
    
    # 连接 Telegram
    log('🔌 连接 Telegram...')
    try:
        from telethon.sync import TelegramClient
        from telethon.sessions import StringSession
        
        client = TelegramClient(StringSession(SESSION_STRING), int(API_ID), API_HASH)
        client.connect()
        
        if not client.is_user_authorized():
            log('❌ 会话无效，请重新登录生成新的会话字符串')
            save_log_to_github()
            return 1
        
        me = client.get_me()
        log(f'   ✅ 已登录: @{me.username}')
    except Exception as e:
        log(f'❌ 连接失败: {e}')
        save_log_to_github()
        return 1
    
    # 获取或创建 Release
    release = get_or_create_release()
    if not release:
        log('❌ 无法创建 Release')
        client.disconnect()
        save_log_to_github()
        return 1
    
    # 同步每个频道
    all_files = []
    for ch in CHANNELS:
        result = sync_channel(client, ch, release, download_dir)
        if result:
            all_files.append(result)
        time.sleep(1)
    
    client.disconnect()
    
    # 加载现有数据
    data, sha = load_remote_data()
    if not data:
        data = {'files': [], 'settings': {
            'title': '资源空间站',
            'subtitle': '精选资源 · 极速下载 · 自动更新',
        }}
    
    # 更新文件列表
    if all_files:
        # 按频道去重，保留最新
        existing = {f.get('channel', ''): f for f in data.get('files', [])}
        for f in all_files:
            existing[f['channel']] = f
        data['files'] = list(existing.values())
        
        # 更新最后同步时间
        data['last_update'] = datetime.now(CST).strftime('%Y-%m-%d %H:%M')
        
        # 保存
        if save_remote_data(data, sha):
            log(f'✅ 数据已更新，共 {len(data["files"])} 个文件')
        else:
            log('❌ 保存数据失败')
    else:
        log('⚠️  没有新文件')
    
    # 清理下载目录
    import shutil
    if os.path.exists(download_dir):
        shutil.rmtree(download_dir, ignore_errors=True)
    
    log('🎉 同步完成')
    save_log_to_github()
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as e:
        import traceback
        log(f'💥 未捕获异常: {e}')
        log(traceback.format_exc())
        save_log_to_github()
        sys.exit(1)
