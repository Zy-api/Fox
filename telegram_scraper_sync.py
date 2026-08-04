#!/usr/bin/env python3
"""
Telegram 自动同步脚本（完整版）
- 多频道支持
- 关键词匹配
- 只保留最新版本
- 自动下载文件上传到GitHub（直链下载）
- 支持 Bot API 和 Web 抓取两种模式
"""
import os
import sys
import json
import time
import base64
import fnmatch
import argparse
import urllib.request
import urllib.error
import ssl
import re
import subprocess
from datetime import datetime, timezone, timedelta

# ========== 配置 ==========
# Telegram 配置
API_ID = os.environ.get('TG_API_ID', '')
API_HASH = os.environ.get('TG_API_HASH', '')
BOT_TOKEN = os.environ.get('TG_BOT_TOKEN', '')

# 频道配置
CHANNELS_CONFIG = os.environ.get('CHANNELS_CONFIG', 
    'PNAyyds|PNA|.zip,.rar,.7z;hhhhp|芒果,客户端|.zip,.rar,.7z,.apk,.ipa')

DEFAULT_FILE_TYPES = '.zip,.rar,.7z,.apk,.ipa'
MAX_PAGES = int(os.environ.get('MAX_PAGES', '20'))
KEEP_LATEST_ONLY = os.environ.get('KEEP_LATEST', 'true').lower() == 'true'

# GitHub 配置
GITHUB_TOKEN = os.environ.get('GITHUB_TOKEN', '')
GITHUB_OWNER = 'Zy-api'
GITHUB_REPO = 'Fox'
GITHUB_BRANCH = 'main'
DATA_FILE = 'pan-data.json'
FILES_DIR = 'files'

STATE_FILE = os.environ.get('STATE_FILE', '/tmp/telegram_sync_state.json')

CST = timezone(timedelta(hours=8))


def log(msg):
    ts = datetime.now(CST).strftime('%Y-%m-%d %H:%M:%S')
    print(f'[{ts}] {msg}', flush=True)


def parse_channels_config():
    channels = []
    if CHANNELS_CONFIG:
        for chan_str in CHANNELS_CONFIG.split(';'):
            chan_str = chan_str.strip()
            if not chan_str:
                continue
            parts = chan_str.split('|')
            channel = parts[0].strip()
            keywords = parts[1].strip() if len(parts) > 1 else ''
            file_types = parts[2].strip() if len(parts) > 2 else DEFAULT_FILE_TYPES
            channels.append({
                'channel': channel,
                'keywords': [k.strip() for k in keywords.split(',') if k.strip()],
                'file_types': file_types
            })
    return channels


def match_filename(filename, keywords=None, pattern=None, file_types=None):
    if not filename:
        return False
    ext = os.path.splitext(filename)[1].lower()
    if file_types:
        allowed_types = [t.strip().lower() for t in file_types.split(',') if t.strip()]
        if allowed_types and ext not in allowed_types:
            return False
    if keywords:
        for kw in keywords:
            if kw and kw.lower() in filename.lower():
                return True
        return False
    if pattern and not fnmatch.fnmatch(filename, pattern):
        return False
    return True


def extract_version(filename):
    match = re.search(r'(\d+(?:\.\d+)+)', filename)
    if match:
        return tuple(int(x) for x in match.group(1).split('.'))
    return (0,)


def get_latest_version(files):
    if not files:
        return None
    sorted_files = sorted(files, key=lambda f: extract_version(f.get('name', '')), reverse=True)
    return sorted_files[0]


def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, 'r') as f:
                return json.load(f)
        except:
            pass
    return {'channels': {}}


def save_state(state):
    try:
        os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
        with open(STATE_FILE, 'w') as f:
            json.dump(state, f, indent=2, ensure_ascii=False)
    except Exception as e:
        log(f'⚠️  保存状态失败: {e}')


# ========== GitHub API ==========

def github_api_request(path, method='GET', data=None):
    url = f'https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/{path}'
    headers = {
        'Authorization': f'token {GITHUB_TOKEN}',
        'Accept': 'application/vnd.github.v3+json',
        'User-Agent': 'Telegram-Sync-Bot'
    }
    body = None
    if data:
        body = json.dumps(data).encode('utf-8')
        headers['Content-Type'] = 'application/json'
    try:
        req = urllib.request.Request(url, data=body, headers=headers, method=method)
        with urllib.request.urlopen(req, timeout=120) as resp:
            return json.loads(resp.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        error_body = e.read().decode('utf-8', errors='ignore')[:500]
        log(f'❌ GitHub API HTTP {e.code}: {error_body[:200]}')
        return None
    except Exception as e:
        log(f'❌ GitHub API 请求失败: {e}')
        return None


def load_remote_data():
    result = github_api_request(f'contents/{DATA_FILE}?ref={GITHUB_BRANCH}')
    if result and 'content' in result:
        try:
            content = base64.b64decode(result['content']).decode('utf-8')
            data = json.loads(content)
            return data, result.get('sha')
        except Exception as e:
            log(f'❌ 解析远程数据失败: {e}')
    return None, None


def save_remote_data(data, sha):
    content = json.dumps(data, indent=2, ensure_ascii=False)
    content_b64 = base64.b64encode(content.encode('utf-8')).decode('utf-8')
    payload = {
        'message': f'auto-sync: 数据更新 - {datetime.now(CST).strftime("%Y-%m-%d %H:%M")}',
        'content': content_b64,
        'branch': GITHUB_BRANCH
    }
    if sha:
        payload['sha'] = sha
    result = github_api_request(f'contents/{DATA_FILE}', 'PUT', payload)
    return result is not None and 'content' in result


def upload_file_to_github(local_path, remote_path):
    try:
        existing = github_api_request(f'contents/{remote_path}?ref={GITHUB_BRANCH}')
        sha = existing.get('sha') if existing and 'sha' in existing else None
        
        with open(local_path, 'rb') as f:
            content = f.read()
        
        content_b64 = base64.b64encode(content).decode('utf-8')
        filename = os.path.basename(local_path)
        
        payload = {
            'message': f'auto-sync: 上传 {filename}',
            'content': content_b64,
            'branch': GITHUB_BRANCH
        }
        if sha:
            payload['sha'] = sha
        
        result = github_api_request(f'contents/{remote_path}', 'PUT', payload)
        return result is not None and 'content' in result
    except Exception as e:
        log(f'❌ 上传文件失败: {e}')
        return False


def delete_file_from_github(remote_path):
    try:
        existing = github_api_request(f'contents/{remote_path}?ref={GITHUB_BRANCH}')
        if not existing or 'sha' not in existing:
            return True
        
        payload = {
            'message': f'auto-sync: 删除旧文件',
            'sha': existing['sha'],
            'branch': GITHUB_BRANCH
        }
        result = github_api_request(f'contents/{remote_path}', 'DELETE', payload)
        return result is not None
    except Exception as e:
        log(f'⚠️  删除文件失败: {e}')
        return False


# ========== Web 抓取模式（获取文件列表） ==========

def fetch_channel_page(channel, after_id=None):
    url = f'https://t.me/s/{channel}'
    if after_id:
        url += f'?after={after_id}'
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
    }
    ssl_contexts = [
        ssl.create_default_context(),
        ssl._create_unverified_context(),
    ]
    for ctx in ssl_contexts:
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=30, context=ctx) as resp:
                return resp.read().decode('utf-8')
        except Exception as e:
            last_error = e
            continue
    log(f'❌ 抓取频道页面失败: {last_error}')
    return None


def get_min_msg_id(html):
    ids = re.findall(r'data-post="[^"]*/(\d+)"', html)
    if not ids:
        ids = re.findall(r'/(\d+)\?embed=1', html)
    if ids:
        return min(int(i) for i in ids)
    return 0


def extract_messages_web(html, channel):
    messages = []
    doc_names = re.findall(r'class="tgme_widget_message_document_title[^"]*"[^>]*>([^<]+)</div>', html)
    doc_sizes = re.findall(r'class="tgme_widget_message_document_extra[^"]*"[^>]*>([^<]+)</div>', html)
    if not doc_names:
        doc_names = re.findall(r'class="tgme_widget_message_document_name[^"]*">([^<]+)</div>', html)
        doc_sizes = re.findall(r'class="tgme_widget_message_document_extra[^"]*">([^<]+)</div>', html)
    
    id_matches = re.findall(r'data-post="[^"]*/(\d+)"', html)
    if not id_matches:
        id_matches = re.findall(r'/(\d+)\?embed=1', html)
    
    date_matches = re.findall(r'class="tgme_widget_message_meta[^"]*">.*?<time[^>]*datetime="([^"]+)"', html, re.DOTALL)
    text_matches = re.findall(r'class="tgme_widget_message_text[^"]*">(.*?)</div>', html, re.DOTALL)
    
    for i, name in enumerate(doc_names):
        name = name.strip()
        size = doc_sizes[i].strip() if i < len(doc_sizes) else ''
        msg_id = id_matches[i] if i < len(id_matches) else ''
        msg_date = date_matches[i] if i < len(date_matches) else ''
        msg_text = ''
        if i < len(text_matches):
            msg_text = re.sub(r'<[^>]+>', '', text_matches[i]).strip()[:100]
        messages.append({
            'msg_id': msg_id,
            'name': name,
            'size': size,
            'date': msg_date,
            'desc': msg_text,
            'channel': channel
        })
    return messages


# ========== Telethon 模式（下载文件） ==========

def sync_with_telethon(channel_config, state):
    """使用 Telethon 同步（能下载文件）"""
    try:
        from telethon import TelegramClient
        from telethon.tl.types import DocumentAttributeFilename
    except ImportError:
        log('   ⚠️  Telethon 未安装，无法下载文件')
        return None
    
    if not API_ID or not API_HASH or not BOT_TOKEN:
        log('   ⚠️  缺少 Telegram API 配置，无法下载文件')
        return None
    
    channel = channel_config['channel']
    keywords = channel_config.get('keywords', [])
    file_types = channel_config.get('file_types', DEFAULT_FILE_TYPES)
    
    chan_state = state['channels'].get(channel, {'last_msg_id': 0, 'uploaded_files': []})
    last_msg_id = chan_state.get('last_msg_id', 0)
    uploaded = set(chan_state.get('uploaded_files', []))
    
    log(f'   🤖 使用 Telethon Bot 模式')
    
    new_files = []
    max_msg_id = last_msg_id
    download_dir = '/tmp/tg_downloads'
    os.makedirs(download_dir, exist_ok=True)
    
    try:
        client = TelegramClient('bot_session', int(API_ID), API_HASH)
        client.start(bot_token=BOT_TOKEN)
        
        # 获取频道实体
        channel_entity = client.get_entity(f'@{channel}')
        log(f'   ✅ 已连接频道 @{channel}')
        
        # 获取消息
        messages = []
        for msg in client.iter_messages(channel_entity, limit=200):
            if msg.file and msg.document:
                # 获取文件名
                filename = None
                for attr in msg.document.attributes:
                    if hasattr(attr, 'file_name'):
                        filename = attr.file_name
                        break
                
                if not filename:
                    continue
                
                if not match_filename(filename, keywords=keywords, file_types=file_types):
                    continue
                
                # 获取文件大小
                size_mb = msg.document.size / (1024 * 1024)
                size_str = f'{size_mb:.1f} MB' if size_mb >= 1 else f'{msg.document.size / 1024:.0f} KB'
                
                messages.append({
                    'msg_id': str(msg.id),
                    'name': filename,
                    'size': size_str,
                    'date': msg.date.isoformat() if msg.date else '',
                    'desc': msg.message[:100] if msg.message else '',
                    'channel': channel,
                    'msg_obj': msg
                })
        
        log(f'   📊 找到 {len(messages)} 个匹配文件')
        
        # 只保留最新版
        if KEEP_LATEST_ONLY and messages:
            latest = get_latest_version(messages)
            if latest:
                messages = [latest]
                log(f'   🆕 保留最新版: {latest["name"]}')
        
        # 下载并上传
        for msg_info in messages:
            name = msg_info['name']
            unique_id = f"{channel}_{name}"
            msg_id = int(msg_info['msg_id'])
            
            if msg_id > max_msg_id:
                max_msg_id = msg_id
            
            if unique_id in uploaded:
                log(f'   ✅ 已上传: {name}')
                # 构造已上传文件的信息
                direct_url = f'https://github.com/{GITHUB_OWNER}/{GITHUB_REPO}/raw/{GITHUB_BRANCH}/{FILES_DIR}/{channel}/{name}'
                file_info = {
                    'name': name,
                    'size': msg_info['size'],
                    'date': msg_info['date'][:10] if msg_info['date'] else datetime.now(CST).strftime('%Y-%m-%d'),
                    'url': direct_url,
                    'direct_url': direct_url,
                    'tag': channel,
                    'desc': msg_info['desc'] or f'来自 @{channel}',
                    'icon': '',
                    'file_unique_id': unique_id,
                    'msg_id': msg_id,
                    'channel': channel,
                    'source': 'telethon'
                }
                new_files.append(file_info)
                continue
            
            log(f'   ⬇️  下载: {name} ({msg_info["size"]})')
            
            # 下载文件
            local_path = os.path.join(download_dir, name)
            msg_obj = msg_info['msg_obj']
            
            try:
                client.download_media(msg_obj, local_path)
                log(f'     ✅ 下载完成')
                
                # 上传到 GitHub
                remote_path = f'{FILES_DIR}/{channel}/{name}'
                log(f'     ☁️  上传到 GitHub...')
                
                if upload_file_to_github(local_path, remote_path):
                    log(f'     ✅ 上传成功！')
                    
                    direct_url = f'https://github.com/{GITHUB_OWNER}/{GITHUB_REPO}/raw/{GITHUB_BRANCH}/{remote_path}'
                    
                    date_str = datetime.now(CST).strftime('%Y-%m-%d')
                    if msg_info['date']:
                        try:
                            date_str = msg_info['date'][:10]
                        except:
                            pass
                    
                    file_info = {
                        'name': name,
                        'size': msg_info['size'],
                        'date': date_str,
                        'url': direct_url,
                        'direct_url': direct_url,
                        'tag': channel,
                        'desc': msg_info['desc'] or f'来自 @{channel}',
                        'icon': '',
                        'file_unique_id': unique_id,
                        'msg_id': msg_id,
                        'channel': channel,
                        'source': 'telethon'
                    }
                    new_files.append(file_info)
                    uploaded.add(unique_id)
                else:
                    log(f'     ❌ 上传失败')
                
                # 清理本地文件
                try:
                    os.remove(local_path)
                except:
                    pass
                    
            except Exception as e:
                log(f'     ❌ 下载失败: {e}')
        
        client.disconnect()
        
    except Exception as e:
        log(f'   ❌ Telethon 错误: {e}')
        import traceback
        traceback.print_exc()
        return None
    
    # 更新状态
    if max_msg_id > last_msg_id:
        chan_state['last_msg_id'] = max_msg_id
    chan_state['uploaded_files'] = list(uploaded)
    state['channels'][channel] = chan_state
    
    return new_files


# ========== Web 抓取模式（不能下载文件） ==========

def sync_with_webscrape(channel_config, state):
    """使用 Web 抓取同步（只能获取链接，不能下载）"""
    channel = channel_config['channel']
    keywords = channel_config.get('keywords', [])
    file_types = channel_config.get('file_types', DEFAULT_FILE_TYPES)
    
    chan_state = state['channels'].get(channel, {'last_msg_id': 0, 'processed_files': []})
    last_msg_id = chan_state.get('last_msg_id', 0)
    processed = set(chan_state.get('processed_files', []))

    log(f'   🌐 使用 Web 抓取模式')

    all_messages = []
    current_after = None
    page = 0

    while page < MAX_PAGES:
        page += 1
        html = fetch_channel_page(channel, current_after)
        if not html:
            break

        messages = extract_messages_web(html, channel)
        if not messages:
            break
        
        all_messages.extend(messages)

        min_id = get_min_msg_id(html)
        if not min_id or min_id >= (current_after or 999999999):
            break
        
        current_after = min_id
        time.sleep(0.5)

    log(f'   📊 共抓取 {len(all_messages)} 个文件')

    # 过滤
    matched_files = []
    seen = set()
    
    for msg in all_messages:
        name = msg['name']
        if not match_filename(name, keywords=keywords, file_types=file_types):
            continue
        key = f"{name}_{msg['size']}"
        if key in seen:
            continue
        seen.add(key)
        matched_files.append(msg)
    
    log(f'   ✅ 匹配 {len(matched_files)} 个文件')

    # 只保留最新版
    if KEEP_LATEST_ONLY and matched_files:
        latest = get_latest_version(matched_files)
        if latest:
            matched_files = [latest]
            log(f'   🆕 保留最新版: {latest["name"]}')

    # 构造文件信息
    new_files = []
    max_msg_id = last_msg_id

    for msg in sorted(matched_files, key=lambda x: int(x.get('msg_id') or 0)):
        name = msg['name']
        unique_id = f"{channel}_{name}"
        
        msg_id = 0
        try:
            if msg['msg_id']:
                msg_id = int(msg['msg_id'])
        except:
            pass
        
        if msg_id > max_msg_id:
            max_msg_id = msg_id
        
        if unique_id in processed:
            log(f'   ✅ 已存在: {name}')
            file_info = {
                'name': name,
                'size': msg['size'],
                'date': msg['date'][:10] if msg['date'] else datetime.now(CST).strftime('%Y-%m-%d'),
                'url': f'https://t.me/{channel}/{msg_id}',
                'direct_url': '',
                'tag': channel,
                'desc': msg['desc'] or f'来自 @{channel}',
                'icon': '',
                'file_unique_id': unique_id,
                'msg_id': msg_id,
                'channel': channel,
                'source': 'webscrape'
            }
            new_files.append(file_info)
            continue
        
        log(f'   📎 发现: {name} ({msg["size"]})')
        
        date_str = datetime.now(CST).strftime('%Y-%m-%d')
        if msg['date']:
            try:
                dt = datetime.fromisoformat(msg['date'].replace('Z', '+00:00'))
                date_str = dt.astimezone(CST).strftime('%Y-%m-%d')
            except:
                pass
        
        file_info = {
            'name': name,
            'size': msg['size'],
            'date': date_str,
            'url': f'https://t.me/{channel}/{msg_id}',
            'direct_url': '',
            'tag': channel,
            'desc': msg['desc'] or f'来自 @{channel}',
            'icon': '',
            'file_unique_id': unique_id,
            'msg_id': msg_id,
            'channel': channel,
            'source': 'webscrape'
        }
        new_files.append(file_info)
        processed.add(unique_id)

    # 更新状态
    if max_msg_id > last_msg_id:
        chan_state['last_msg_id'] = max_msg_id
    chan_state['processed_files'] = list(processed)
    state['channels'][channel] = chan_state

    return new_files


# ========== 主同步逻辑 ==========

def sync_once():
    state = load_state()
    if 'channels' not in state:
        state['channels'] = {}
    
    channels = parse_channels_config()
    log(f'🔍 配置了 {len(channels)} 个频道')
    if KEEP_LATEST_ONLY:
        log(f'   模式: 每个频道只保留最新版')
    
    use_telethon = API_ID and API_HASH and BOT_TOKEN
    if use_telethon:
        log(f'   下载: Telethon Bot 模式（直链下载）')
    else:
        log(f'   下载: Web 抓取模式（仅链接，配置Bot后可直链下载）')

    all_new_files = []

    for chan_config in channels:
        channel = chan_config['channel']
        log(f'\n📡 频道: @{channel}')
        log(f'   关键词: {chan_config.get("keywords", []) if chan_config.get("keywords") else "(全部)"}')
        
        new_files = None
        
        # 先尝试 Telethon 模式
        if use_telethon:
            new_files = sync_with_telethon(chan_config, state)
        
        # 如果 Telethon 失败或不可用，用 Web 抓取
        if new_files is None:
            new_files = sync_with_webscrape(chan_config, state)
        
        if new_files:
            all_new_files.extend(new_files)

    log(f'\n🎉 所有频道共处理 {len(all_new_files)} 个文件！')

    if all_new_files and GITHUB_TOKEN:
        remote_data, sha = load_remote_data()
        if remote_data is None:
            remote_data = {'files': [], 'settings': {}}

        existing_files = remote_data.get('files', [])
        
        # 如果只保留最新版，按频道替换
        if KEEP_LATEST_ONLY:
            for nf in all_new_files:
                ch = nf.get('channel', 'unknown')
                # 删除旧文件
                old_files = [f for f in existing_files if f.get('channel') == ch]
                for old in old_files:
                    old_name = old.get('name', '')
                    if old_name and old_name != nf['name']:
                        old_path = f'{FILES_DIR}/{ch}/{old_name}'
                        log(f'   🗑️  删除旧文件: {old_name}')
                        delete_file_from_github(old_path)
                
                # 移除旧记录
                existing_files = [f for f in existing_files if f.get('channel') != ch]
                # 添加新文件
                existing_files.insert(0, nf)
        else:
            existing_ids = {f.get('file_unique_id') for f in existing_files if f.get('file_unique_id')}
            for nf in all_new_files:
                if nf['file_unique_id'] not in existing_ids:
                    existing_files.insert(0, nf)
                    existing_ids.add(nf['file_unique_id'])

        remote_data['files'] = existing_files
        if save_remote_data(remote_data, sha):
            log(f'✅ 数据已更新到 GitHub')
        else:
            log(f'❌ 保存到 GitHub 失败')

    save_state(state)
    return len(all_new_files)


def main():
    parser = argparse.ArgumentParser(description='Telegram 自动同步（完整版）')
    parser.add_argument('--once', action='store_true', help='运行一次同步')
    parser.add_argument('--test', action='store_true', help='测试模式')
    args = parser.parse_args()

    if args.test:
        log('🧪 测试模式')
        if API_ID and API_HASH and BOT_TOKEN:
            log('✅ Telethon 配置齐全')
        else:
            log('⚠️  Telethon 配置不完整，将使用 Web 抓取模式')
            log('   需要: TG_API_ID, TG_API_HASH, TG_BOT_TOKEN')
        channels = parse_channels_config()
        for chan in channels:
            log(f'\n📡 @{chan["channel"]}')
            html = fetch_channel_page(chan['channel'])
            if html:
                msgs = extract_messages_web(html, chan['channel'])
                log(f'找到 {len(msgs)} 个文件:')
                for m in msgs[:5]:
                    match_str = '✅' if match_filename(m['name'], keywords=chan.get('keywords', []), file_types=chan.get('file_types')) else '❌'
                    log(f'  {match_str} {m["name"]} ({m["size"]})')
        return

    sync_once()


if __name__ == '__main__':
    main()
