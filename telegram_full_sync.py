#!/usr/bin/env python3
"""
Telegram 公开频道自动同步 + 直接下载脚本
使用 Playwright 浏览器下载文件，上传到 GitHub Releases
"""
import os
import sys
import json
import re
import time
import base64
import fnmatch
import argparse
import urllib.request
import urllib.error
import urllib.parse
import ssl
from datetime import datetime, timezone, timedelta

# ========== 配置 ==========
CHANNELS = [
    {'username': 'PNAyyds', 'keywords': ['PNA', 'PAN'], 'tag': '💜'},
    {'username': 'hhhhhp', 'keywords': ['芒果', '客户端'], 'tag': '🥭'},
]

GITHUB_TOKEN = os.environ.get('GITHUB_TOKEN', '')
GITHUB_OWNER = 'Zy-api'
GITHUB_REPO = 'Fox'
GITHUB_BRANCH = 'main'
DATA_FILE = 'pan-data.json'

STATE_FILE = '/tmp/telegram_sync_state.json'
CST = timezone(timedelta(hours=8))


def log(msg):
    ts = datetime.now(CST).strftime('%Y-%m-%d %H:%M:%S')
    print(f'[{ts}] {msg}', flush=True)


def match_keywords(filename, keywords):
    filename_lower = filename.lower()
    for kw in keywords:
        if kw.lower() in filename_lower:
            return True
    return False


def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, 'r') as f:
                return json.load(f)
        except:
            pass
    return {'processed_files': [], 'last_sync': ''}


def save_state(state):
    try:
        os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
        with open(STATE_FILE, 'w') as f:
            json.dump(state, f, indent=2, ensure_ascii=False)
    except Exception as e:
        log(f'⚠️  保存状态失败: {e}')


def github_api_request(path, method='GET', data=None, content_type='application/json', raw_url=None):
    url = raw_url if raw_url else f'https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/{path}'
    headers = {
        'Authorization': f'token {GITHUB_TOKEN}',
        'Accept': 'application/vnd.github.v3+json',
        'User-Agent': 'Telegram-Sync-Bot'
    }
    if content_type:
        headers['Content-Type'] = content_type
    
    body = None
    if data:
        if isinstance(data, str):
            body = data.encode('utf-8')
        elif isinstance(data, bytes):
            body = data
        else:
            body = json.dumps(data).encode('utf-8')
    
    try:
        req = urllib.request.Request(url, data=body, headers=headers, method=method)
        with urllib.request.urlopen(req, timeout=120) as resp:
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
        'message': f'auto-sync: 资源同步更新 - {datetime.now(CST).strftime("%Y-%m-%d %H:%M")}',
        'content': content_b64,
        'branch': GITHUB_BRANCH
    }
    if sha:
        payload['sha'] = sha
    result = github_api_request(f'contents/{DATA_FILE}', 'PUT', payload)
    return result is not None and 'content' in result


def get_or_create_release():
    release_tag = 'telegram-files'
    release = github_api_request(f'releases/tags/{release_tag}')
    
    if not release:
        log('📦 创建 GitHub Release...')
        release = github_api_request('releases', 'POST', {
            'tag_name': release_tag,
            'name': '📦 资源文件库',
            'body': '自动同步的 Telegram 频道资源文件，支持直接下载',
            'draft': False,
            'prerelease': False,
        })
    
    return release


def upload_file_to_release(file_path, file_name, release):
    upload_url = release.get('upload_url', '').replace('{?name,label}', f'?name={urllib.parse.quote(file_name)}')
    
    if not upload_url:
        log('❌ 无法获取上传地址')
        return None
    
    log(f'⬆️  上传文件: {file_name}')
    
    try:
        file_size = os.path.getsize(file_path)
        log(f'   文件大小: {file_size / 1024 / 1024:.1f} MB')
        
        with open(file_path, 'rb') as f:
            file_data = f.read()
        
        result = github_api_request(
            '',
            'POST',
            data=file_data,
            content_type='application/octet-stream',
            raw_url=upload_url
        )
        
        if result and 'browser_download_url' in result:
            log(f'   ✅ 上传成功')
            return result['browser_download_url']
        else:
            log(f'   ❌ 上传失败')
            return None
    except Exception as e:
        log(f'❌ 上传文件失败: {e}')
        return None


def delete_release_asset(asset_id):
    return github_api_request(f'releases/assets/{asset_id}', 'DELETE')


def fetch_channel_files(channel_username):
    log(f'🔍 抓取 @{channel_username} 频道...')
    
    all_messages = []
    after_id = None
    max_pages = 15
    page = 0
    
    ctx = ssl._create_unverified_context()
    
    while page < max_pages:
        page += 1
        url = f'https://t.me/s/{channel_username}'
        if after_id:
            url += f'?after={after_id}'
        
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
        }
        
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=30, context=ctx) as resp:
                html = resp.read().decode('utf-8')
        except Exception as e:
            log(f'❌ 抓取失败 (第{page}页): {e}')
            break
        
        doc_names = re.findall(r'class="tgme_widget_message_document_name[^"]*">([^<]+)</div>', html)
        doc_sizes = re.findall(r'class="tgme_widget_message_document_extra[^"]*">([^<]+)</div>', html)
        msg_ids = re.findall(r'data-post="([^"]+)"', html)
        msg_links = re.findall(r'href="(https://t\.me/[^"]+/\d+)"', html)
        date_matches = re.findall(r'<time[^>]*datetime="([^"]+)"', html)
        text_matches = re.findall(r'class="tgme_widget_message_text[^"]*">(.*?)</div>', html, re.DOTALL)
        
        if not doc_names:
            break
        
        for i, name in enumerate(doc_names):
            name = name.strip()
            size = doc_sizes[i].strip() if i < len(doc_sizes) else ''
            msg_id = msg_ids[i] if i < len(msg_ids) else ''
            link = msg_links[i] if i < len(msg_links) else ''
            date_str = date_matches[i] if i < len(date_matches) else ''
            text = text_matches[i] if i < len(text_matches) else ''
            desc = re.sub(r'<[^>]+>', '', text).strip()[:100] if text else ''
            
            all_messages.append({
                'msg_id': msg_id,
                'name': name,
                'size': size,
                'link': link,
                'date': date_str,
                'desc': desc,
                'channel': channel_username,
            })
        
        if msg_ids:
            last_id = msg_ids[-1].split('/')[-1]
            try:
                last_id_num = int(last_id)
                if after_id and last_id_num <= int(after_id):
                    break
                after_id = str(last_id_num - 1)
            except:
                break
        else:
            break
        
        time.sleep(0.3)
    
    log(f'   找到 {len(all_messages)} 个文件')
    return all_messages


def download_file_with_playwright(telegram_url, save_path):
    """用 Playwright 下载 Telegram 文件"""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        log('⚠️  Playwright 未安装')
        return False
    
    log(f'   🌐 打开页面: {telegram_url}')
    
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            context = browser.new_context(
                user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                accept_downloads=True,
            )
            page = context.new_page()
            
            try:
                page.goto(telegram_url, wait_until='domcontentloaded', timeout=30000)
            except:
                pass
            
            time.sleep(3)
            
            # 尝试找下载按钮
            download_triggered = False
            
            # 方法1: 找下载按钮
            selectors = [
                'a.tgme_widget_message_download_button',
                '.tgme_widget_message_document_action_download',
                'a[download]',
                '.tgme_widget_message_document a',
            ]
            
            for selector in selectors:
                try:
                    element = page.query_selector(selector)
                    if element:
                        log(f'   找到下载按钮: {selector}')
                        # 获取 href
                        href = element.get_attribute('href')
                        if href and href.startswith('http'):
                            log(f'   下载链接: {href[:80]}...')
                            # 直接用 urllib 下载
                            ctx = ssl._create_unverified_context()
                            headers = {
                                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                                'Referer': 'https://t.me/',
                            }
                            req = urllib.request.Request(href, headers=headers)
                            with urllib.request.urlopen(req, timeout=300, context=ctx) as resp:
                                with open(save_path, 'wb') as f:
                                    while True:
                                        chunk = resp.read(8192)
                                        if not chunk:
                                            break
                                        f.write(chunk)
                            download_triggered = True
                            break
                except:
                    continue
            
            # 方法2: 点击按钮触发下载
            if not download_triggered:
                try:
                    with page.expect_download(timeout=30000) as download_info:
                        for selector in selectors:
                            try:
                                element = page.query_selector(selector)
                                if element:
                                    element.click()
                                    break
                            except:
                                continue
                    download = download_info.value
                    download.save_as(save_path)
                    download_triggered = True
                except Exception as e:
                    log(f'   点击下载失败: {e}')
            
            browser.close()
            
            if download_triggered and os.path.exists(save_path):
                size_mb = os.path.getsize(save_path) / 1024 / 1024
                log(f'   ✅ 下载成功 ({size_mb:.1f} MB)')
                return True
            
            return False
    except Exception as e:
        log(f'❌ Playwright 错误: {e}')
        return False


def sync_once(download=True):
    state = load_state()
    processed = set(state.get('processed_files', []))

    log('🚀 开始 Telegram 同步')
    log(f'   频道数: {len(CHANNELS)}')
    log(f'   下载模式: {"开启" if download else "关闭"}')

    all_new_files = []

    for channel in CHANNELS:
        ch_name = channel['username']
        keywords = channel['keywords']
        tag = channel['tag']
        
        log(f'\n📡 频道: @{ch_name}')
        
        messages = fetch_channel_files(ch_name)
        
        for msg in messages:
            name = msg['name']
            
            if not match_keywords(name, keywords):
                continue
            
            unique_id = f"{ch_name}_{name}"
            
            if unique_id in processed:
                continue
            
            processed.add(unique_id)
            
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
                'download_url': '',
                'tag': f'{tag}',
                'desc': msg['desc'] or f'来自 @{ch_name}',
                'icon': '',
                'file_unique_id': unique_id,
                'msg_id': msg['msg_id'],
                'channel': ch_name,
                'source': 'telegram'
            }
            all_new_files.append(file_info)
            log(f'  ✨ {tag} {name} ({msg["size"]})')

    # 只保留每个频道最新版
    if all_new_files:
        log(f'\n📊 发现 {len(all_new_files)} 个匹配文件，筛选最新版...')
        
        latest_by_channel = {}
        for f in all_new_files:
            ch = f['channel']
            try:
                msg_id = int(f['msg_id'].split('/')[-1]) if f['msg_id'] else 0
            except:
                msg_id = 0
            
            if ch not in latest_by_channel or msg_id > latest_by_channel[ch].get('_msg_id_num', 0):
                f['_msg_id_num'] = msg_id
                latest_by_channel[ch] = f
        
        latest_files = list(latest_by_channel.values())
        log(f'   保留 {len(latest_files)} 个最新版')

        # 下载文件并上传
        if download and GITHUB_TOKEN:
            release = get_or_create_release()
            if release:
                # 删除旧的同频道文件
                existing_assets = release.get('assets', [])
                for f in latest_files:
                    ch = f['channel']
                    for asset in existing_assets:
                        if ch in asset.get('name', ''):
                            log(f'   删除旧文件: {asset["name"]}')
                            delete_release_asset(asset['id'])
                
                # 下载并上传新文件
                os.makedirs('/tmp/downloads', exist_ok=True)
                
                for f in latest_files:
                    safe_name = f['name'].replace(' ', '_')
                    local_path = f'/tmp/downloads/{f["channel"]}_{safe_name}'
                    
                    log(f'\n📥 下载: {f["name"]}')
                    success = download_file_with_playwright(f['url'], local_path)
                    
                    if success and os.path.exists(local_path):
                        upload_name = f'{f["channel"]}_{safe_name}'
                        dl_url = upload_file_to_release(local_path, upload_name, release)
                        if dl_url:
                            f['download_url'] = dl_url
                            log(f'   🔗 下载链接: {dl_url[:60]}...')
                    
                    time.sleep(1)

        # 更新 GitHub 数据
        if GITHUB_TOKEN:
            remote_data, sha = load_remote_data()
            if remote_data is None:
                remote_data = {'files': [], 'settings': {}}

            existing_files = remote_data.get('files', [])
            
            # 移除旧版本
            updated_channels = set(f['channel'] for f in latest_files)
            existing_files = [f for f in existing_files if f.get('channel') not in updated_channels]
            
            # 添加新版本
            for nf in latest_files:
                nf.pop('_msg_id_num', None)
                existing_files.insert(0, nf)

            remote_data['files'] = existing_files
            if save_remote_data(remote_data, sha):
                log(f'\n✅ 同步完成！共 {len(latest_files)} 个最新版文件')
            else:
                log(f'❌ 保存失败')
        else:
            log('⚠️  无 GitHub Token')
    else:
        log('📭 没有新文件')

    state['processed_files'] = list(processed)
    state['last_sync'] = datetime.now(CST).isoformat()
    save_state(state)

    return len(all_new_files)


def main():
    parser = argparse.ArgumentParser(description='Telegram 同步 + 下载')
    parser.add_argument('--once', action='store_true', help='运行一次')
    parser.add_argument('--no-download', action='store_true', help='不下载文件')
    args = parser.parse_args()

    sync_once(download=not args.no_download)


if __name__ == '__main__':
    main()
