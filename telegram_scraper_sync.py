#!/usr/bin/env python3
"""
Telegram 公开频道自动同步脚本（网页抓取版）
不需要 Telegram API ID，不需要 Bot，不需要登录
直接抓取公开频道的网页版 t.me/s/频道名
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
from datetime import datetime, timezone, timedelta

# ========== 配置 ==========
CHANNEL_USERNAME = os.environ.get('TG_CHANNEL', 'PNAyyds')
FILE_PATTERN = os.environ.get('FILE_PATTERN', 'PAN-*.zip')
FILE_TYPES = os.environ.get('FILE_TYPES', '.zip,.rar,.7z')

GITHUB_TOKEN = os.environ.get('GITHUB_TOKEN', '')
GITHUB_OWNER = 'Zy-api'
GITHUB_REPO = 'Fox'
GITHUB_BRANCH = 'main'
DATA_FILE = 'pan-data.json'

STATE_FILE = os.environ.get('STATE_FILE', '/tmp/telegram_scraper_state.json')

CST = timezone(timedelta(hours=8))


def log(msg):
    ts = datetime.now(CST).strftime('%Y-%m-%d %H:%M:%S')
    print(f'[{ts}] {msg}', flush=True)


def match_filename(filename):
    if not filename:
        return False
    ext = os.path.splitext(filename)[1].lower()
    allowed_types = [t.strip().lower() for t in FILE_TYPES.split(',') if t.strip()]
    if allowed_types and ext not in allowed_types:
        return False
    if FILE_PATTERN and not fnmatch.fnmatch(filename, FILE_PATTERN):
        return False
    return True


def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, 'r') as f:
                return json.load(f)
        except:
            pass
    return {'last_msg_id': 0, 'processed_files': []}


def save_state(state):
    try:
        os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
        with open(STATE_FILE, 'w') as f:
            json.dump(state, f, indent=2, ensure_ascii=False)
    except Exception as e:
        log(f'⚠️  保存状态失败: {e}')


def fetch_channel_page(after_id=None):
    url = f'https://t.me/s/{CHANNEL_USERNAME}'
    if after_id:
        url += f'?after={after_id}'
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
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


def extract_messages(html):
    messages = []
    
    log(f'   HTML长度: {len(html)} 字符')
    
    # 方法1: 匹配文件名称
    doc_names = re.findall(r'class="tgme_widget_message_document_name[^"]*">([^<]+)</div>', html)
    doc_sizes = re.findall(r'class="tgme_widget_message_document_extra[^"]*">([^<]+)</div>', html)
    log(f'   方法1找到文件: {len(doc_names)} 个')
    
    # 方法2: 尝试匹配其他可能的文件类名
    if not doc_names:
        doc_names = re.findall(r'tgme_widget_message_document[^>]*>([^<]+)</', html)
        log(f'   方法2找到文件: {len(doc_names)} 个')
    
    # 方法3: 从消息文本中提取文件名
    if not doc_names:
        text_blocks = re.findall(r'class="tgme_widget_message_text[^"]*">(.*?)</div>', html, re.DOTALL)
        for text in text_blocks:
            clean_text = re.sub(r'<[^>]+>', '', text).strip()
            file_matches = re.findall(r'[\w\-]+\.(?:zip|rar|7z|ZIP|RAR|7Z)', clean_text)
            for fm in file_matches:
                doc_names.append(fm)
                doc_sizes.append('')
        log(f'   方法3从文本提取文件: {len(doc_names)} 个')
    
    # 提取消息ID
    id_matches = re.findall(r'data-post="([^"]+)"', html)
    if not id_matches:
        id_matches = re.findall(r'/(\d+)\?embed=1', html)
    
    # 提取消息链接
    msg_links = re.findall(r'href="(https://t\.me/[^"]+/\d+)"', html)
    
    # 提取消息日期
    date_matches = re.findall(r'class="tgme_widget_message_meta[^"]*">.*?<time[^>]*datetime="([^"]+)"', html, re.DOTALL)
    
    # 提取消息文本
    text_matches = re.findall(r'class="tgme_widget_message_text[^"]*">(.*?)</div>', html, re.DOTALL)
    
    # 组合消息
    for i, name in enumerate(doc_names):
        name = name.strip()
        size = doc_sizes[i].strip() if i < len(doc_sizes) else ''
        
        msg_id = ''
        msg_link = ''
        msg_date = ''
        msg_text = ''
        
        if i < len(id_matches):
            msg_id = id_matches[i]
        if i < len(msg_links):
            msg_link = msg_links[i]
        if i < len(date_matches):
            msg_date = date_matches[i]
        if i < len(text_matches):
            msg_text = re.sub(r'<[^>]+>', '', text_matches[i]).strip()[:100]
        
        messages.append({
            'msg_id': msg_id,
            'name': name,
            'size': size,
            'link': msg_link or f'https://t.me/{CHANNEL_USERNAME}/{msg_id}',
            'date': msg_date,
            'desc': msg_text
        })
    
    return messages


def github_api_request(path, method='GET', data=None):
    url = f'https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/{path}'
    headers = {
        'Authorization': f'token {GITHUB_TOKEN}',
        'Accept': 'application/vnd.github.v3+json',
        'User-Agent': 'Telegram-Sync-Scraper'
    }
    body = None
    if data:
        body = json.dumps(data).encode('utf-8')
        headers['Content-Type'] = 'application/json'
    try:
        req = urllib.request.Request(url, data=body, headers=headers, method=method)
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        error_body = e.read().decode('utf-8', errors='ignore')[:500]
        log(f'❌ GitHub API HTTP {e.code}: {error_body}')
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
        'message': f'auto-sync: Telegram同步更新 - {datetime.now(CST).strftime("%Y-%m-%d %H:%M")}',
        'content': content_b64,
        'branch': GITHUB_BRANCH
    }
    if sha:
        payload['sha'] = sha
    result = github_api_request(f'contents/{DATA_FILE}', 'PUT', payload)
    return result is not None and 'content' in result


def sync_once():
    state = load_state()
    last_msg_id = state.get('last_msg_id', 0)
    processed = set(state.get('processed_files', []))

    log(f'🔍 开始抓取频道 @{CHANNEL_USERNAME}')
    log(f'   匹配规则: {FILE_PATTERN}')
    log(f'   文件类型: {FILE_TYPES}')

    html = fetch_channel_page()
    if not html:
        log('❌ 无法获取频道内容')
        return 0

    messages = extract_messages(html)
    log(f'   找到 {len(messages)} 个文件')

    new_files = []
    max_msg_id = last_msg_id

    for msg in reversed(messages):
        name = msg['name']
        
        if not match_filename(name):
            continue
        
        unique_id = f"{name}_{msg['size']}"
        
        if unique_id in processed:
            continue
        
        msg_id = 0
        try:
            if msg['msg_id']:
                msg_id = int(msg['msg_id'].split('/')[-1])
        except:
            pass
        
        if msg_id > max_msg_id:
            max_msg_id = msg_id
        
        log(f'  ✨ {name} ({msg["size"]})')
        
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
            'url': msg['link'],
            'tag': '最新',
            'desc': msg['desc'] or f'来自 @{CHANNEL_USERNAME}',
            'icon': '',
            'file_unique_id': unique_id,
            'msg_id': msg_id,
            'source': 'telegram_scraper'
        }
        new_files.append(file_info)
        processed.add(unique_id)

    if new_files:
        log(f'\n🎉 发现 {len(new_files)} 个新文件！')

        if GITHUB_TOKEN:
            remote_data, sha = load_remote_data()
            if remote_data is None:
                remote_data = {'files': [], 'settings': {}}

            existing_files = remote_data.get('files', [])
            existing_ids = {f.get('file_unique_id') for f in existing_files if f.get('file_unique_id')}

            added = 0
            for nf in new_files:
                if nf['file_unique_id'] not in existing_ids:
                    existing_files.insert(0, nf)
                    existing_ids.add(nf['file_unique_id'])
                    added += 1

            if added > 0:
                remote_data['files'] = existing_files
                if save_remote_data(remote_data, sha):
                    log(f'✅ 成功添加 {added} 个新文件到 GitHub')
                else:
                    log(f'❌ 保存到 GitHub 失败')
            else:
                log(f'📋 没有新文件需要添加')
        else:
            log('⚠️  未配置 GITHUB_TOKEN，跳过 GitHub 更新')
            for f in new_files:
                log(f'   - {f["name"]}: {f["url"]}')
    else:
        log('📭 没有新的匹配文件')

    if max_msg_id > last_msg_id:
        state['last_msg_id'] = max_msg_id
    state['processed_files'] = list(processed)
    save_state(state)

    return len(new_files)


def main():
    parser = argparse.ArgumentParser(description='Telegram 公开频道同步（网页抓取版）')
    parser.add_argument('--once', action='store_true', help='运行一次同步')
    parser.add_argument('--test', action='store_true', help='测试模式')
    args = parser.parse_args()

    if args.test:
        log('🧪 测试模式')
        html = fetch_channel_page()
        if html:
            msgs = extract_messages(html)
            log(f'找到 {len(msgs)} 个文件:')
            for m in msgs:
                match_str = '✅' if match_filename(m['name']) else '❌'
                log(f'  {match_str} {m["name"]} ({m["size"]})')
        return

    sync_once()


if __name__ == '__main__':
    main()
