#!/usr/bin/env python3
"""
Telegram 公开频道自动同步 + 直接下载脚本 v4
修复频道抓取问题
"""
import os
import sys
import json
import re
import time
import base64
import subprocess
import urllib.request
import urllib.error
import urllib.parse
import ssl
import traceback
from datetime import datetime, timezone, timedelta

# ========== 配置 ==========
CHANNELS = [
    {'username': 'PNAyyds', 'keywords': ['PNA', 'PAN'], 'tag': '💜'},
    {'username': 'hhhhp', 'keywords': ['芒果', '客户端'], 'tag': '🥭'},
]

GITHUB_TOKEN = os.environ.get('GITHUB_TOKEN', '')
GITHUB_OWNER = 'Zy-api'
GITHUB_REPO = 'Fox'
GITHUB_BRANCH = 'main'
DATA_FILE = 'pan-data.json'
LOG_FILE = 'sync-log.txt'

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


def run_cmd(cmd):
    log(f'   执行: {cmd[:80]}...')
    try:
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=300)
        if result.returncode == 0:
            log(f'   ✅ 成功')
            return True, result.stdout
        else:
            log(f'   ❌ 失败 (code={result.returncode})')
            if result.stderr.strip():
                for line in result.stderr.strip().split('\n')[-5:]:
                    log(f'     {line[:100]}')
            return False, result.stderr
    except Exception as e:
        log(f'   ❌ 异常: {e}')
        return False, str(e)


def install_playwright():
    log('📦 安装 Playwright...')
    ok, _ = run_cmd(f'{sys.executable} -m pip install playwright --quiet')
    if not ok:
        return False
    log('📦 安装系统依赖...')
    run_cmd(f'{sys.executable} -m playwright install-deps chromium')
    log('📦 安装 Chromium...')
    ok, _ = run_cmd(f'{sys.executable} -m playwright install chromium')
    if not ok:
        return False
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            b = p.chromium.launch()
            b.close()
        log('✅ Playwright 就绪')
        return True
    except Exception as e:
        log(f'❌ Playwright 验证失败: {e}')
        return False


def match_keywords(filename, keywords):
    filename_lower = filename.lower()
    for kw in keywords:
        if kw.lower() in filename_lower:
            return True
    return False


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
            log(f'   ✅ 上传成功')
            return result['browser_download_url']
        return None
    except Exception as e:
        log(f'❌ 上传失败: {e}')
        return None


def delete_release_asset(asset_id):
    result = github_api_request(f'releases/assets/{asset_id}', 'DELETE')
    return result is not None


def fetch_channel_files_urllib(channel_username):
    """方法1: 用 urllib 抓取"""
    log(f'   方法1: urllib 抓取...')
    
    all_messages = []
    after_id = None
    max_pages = 10
    page = 0
    ctx = ssl._create_unverified_context()
    
    user_agents = [
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1',
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15',
    ]
    
    for ua in user_agents:
        all_messages = []
        after_id = None
        page = 0
        
        while page < max_pages:
            page += 1
            url = f'https://t.me/s/{channel_username}'
            if after_id:
                url += f'?after={after_id}'
            
            headers = {'User-Agent': ua, 'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8', 'Accept': 'text/html,application/xhtml+xml'}
            
            try:
                req = urllib.request.Request(url, headers=headers)
                with urllib.request.urlopen(req, timeout=20, context=ctx) as resp:
                    html = resp.read().decode('utf-8')
            except Exception as e:
                log(f'     UA{user_agents.index(ua)} 第{page}页失败: {e}')
                break
            
            if page == 1:
                log(f'     UA{user_agents.index(ua)} HTML长度: {len(html)}')
                # 检查是否包含文件相关内容
                if 'document' in html.lower():
                    log(f'     包含document关键词')
            
            # 多种正则匹配文件名
            patterns = [
                r'class="tgme_widget_message_document_name[^"]*">([^<]+)</div>',
                r'class="document_name[^"]*">([^<]+)</',
                r'<div class="[^"]*document[^"]*"[^>]*>([^<]+)</div>',
            ]
            
            doc_names = []
            for pat in patterns:
                doc_names = re.findall(pat, html)
                if doc_names:
                    break
            
            if not doc_names:
                if page == 1:
                    log(f'     UA{user_agents.index(ua)} 没找到文件，换下一个UA')
                break
            
            doc_sizes = re.findall(r'class="tgme_widget_message_document_extra[^"]*">([^<]+)</div>', html)
            if not doc_sizes:
                doc_sizes = re.findall(r'class="document_size[^"]*">([^<]+)</', html)
            
            msg_ids = re.findall(r'data-post="([^"]+)"', html)
            msg_links = re.findall(r'href="(https://t\.me/[^"]+/\d+)"', html)
            date_matches = re.findall(r'<time[^>]*datetime="([^"]+)"', html)
            
            for i, name in enumerate(doc_names):
                name = name.strip()
                size = doc_sizes[i].strip() if i < len(doc_sizes) else ''
                msg_id = msg_ids[i] if i < len(msg_ids) else ''
                link = msg_links[i] if i < len(msg_links) else ''
                date_str = date_matches[i] if i < len(date_matches) else ''
                
                all_messages.append({
                    'msg_id': msg_id, 'name': name, 'size': size,
                    'link': link, 'date': date_str, 'desc': '',
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
        
        if all_messages:
            log(f'   ✅ UA{user_agents.index(ua)} 找到 {len(all_messages)} 个文件')
            return all_messages
    
    return []


def fetch_channel_files_playwright(channel_username):
    """方法2: 用 Playwright 抓取"""
    log(f'   方法2: Playwright 抓取...')
    
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        log('   ❌ Playwright 未安装')
        return []
    
    all_messages = []
    
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(
                user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
            )
            
            url = f'https://t.me/s/{channel_username}'
            log(f'     访问: {url}')
            
            try:
                page.goto(url, wait_until='networkidle', timeout=30000)
            except:
                pass
            
            time.sleep(5)
            
            title = page.title()
            log(f'     页面标题: {title}')
            
            html = page.content()
            log(f'     HTML长度: {len(html)}')
            
            # 找文件
            doc_names = re.findall(r'class="tgme_widget_message_document_name[^"]*">([^<]+)</div>', html)
            log(f'     找到文件: {len(doc_names)} 个')
            
            if not doc_names:
                # 截图看看
                try:
                    page.screenshot(path='/tmp/tg_channel.png', full_page=True)
                    log('     已截图')
                except:
                    pass
                browser.close()
                return []
            
            doc_sizes = re.findall(r'class="tgme_widget_message_document_extra[^"]*">([^<]+)</div>', html)
            msg_ids = re.findall(r'data-post="([^"]+)"', html)
            msg_links = re.findall(r'href="(https://t\.me/[^"]+/\d+)"', html)
            date_matches = re.findall(r'<time[^>]*datetime="([^"]+)"', html)
            
            for i, name in enumerate(doc_names):
                name = name.strip()
                size = doc_sizes[i].strip() if i < len(doc_sizes) else ''
                msg_id = msg_ids[i] if i < len(msg_ids) else ''
                link = msg_links[i] if i < len(msg_links) else ''
                date_str = date_matches[i] if i < len(date_matches) else ''
                
                all_messages.append({
                    'msg_id': msg_id, 'name': name, 'size': size,
                    'link': link, 'date': date_str, 'desc': '',
                    'channel': channel_username,
                })
            
            browser.close()
            log(f'   ✅ 找到 {len(all_messages)} 个文件')
            return all_messages
            
    except Exception as e:
        log(f'   ❌ Playwright 抓取失败: {e}')
        traceback.print_exc()
        return []


def fetch_channel_files(channel_username):
    log(f'🔍 抓取 @{channel_username} 频道...')
    
    # 先试 urllib
    messages = fetch_channel_files_urllib(channel_username)
    if messages:
        return messages
    
    # 再试 Playwright
    messages = fetch_channel_files_playwright(channel_username)
    if messages:
        return messages
    
    log(f'   ❌ 所有方法都没抓到文件')
    return []


def download_file_playwright(telegram_url, save_path):
    log(f'   🌐 下载: {telegram_url}')
    
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        log('   ❌ Playwright 未安装')
        return False
    
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(
                user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                accept_downloads=True,
            )
            page = context.new_page()
            
            log('   加载页面...')
            try:
                resp = page.goto(telegram_url, wait_until='networkidle', timeout=30000)
                log(f'   状态: {resp.status if resp else "N/A"}')
            except Exception as e:
                log(f'   ⚠️  加载警告: {e}')
            
            time.sleep(5)
            
            html = page.content()
            log(f'   HTML: {len(html)} 字符')
            
            # 找所有下载相关的链接
            all_links = re.findall(r'href="([^"]+)"[^>]*download', html)
            log(f'   带download属性的链接: {len(all_links)}')
            for l in all_links[:3]:
                log(f'     {l[:80]}')
            
            # 找 cdn 链接
            cdn_links = re.findall(r'https?://cdn[^"\']+', html, re.IGNORECASE)
            log(f'   CDN链接: {len(cdn_links)}')
            for l in cdn_links[:3]:
                log(f'     {l[:80]}')
            
            # 找 file 链接
            file_links = re.findall(r'https?://[^"\']+file[^"\']*', html, re.IGNORECASE)
            log(f'   file链接: {len(file_links)}')
            
            # 尝试下载
            download_url = None
            
            # 方法1: 带 download 属性的链接
            if all_links:
                download_url = all_links[0]
                if not download_url.startswith('http'):
                    download_url = 'https://t.me' + download_url
            
            # 方法2: CDN链接
            if not download_url and cdn_links:
                download_url = cdn_links[0]
            
            # 方法3: 找下载按钮
            if not download_url:
                selectors = [
                    'a.tgme_widget_message_download_button',
                    'a[download]',
                    '.tgme_widget_message_document_action_download',
                    '.tgme_widget_message_document_wrap a',
                ]
                for sel in selectors:
                    try:
                        el = page.query_selector(sel)
                        if el:
                            href = el.get_attribute('href')
                            if href and href.startswith('http'):
                                download_url = href
                                log(f'   找到按钮: {sel}')
                                break
                    except:
                        continue
            
            # 直接下载
            if download_url:
                log(f'   📥 下载: {download_url[:80]}...')
                ctx = ssl._create_unverified_context()
                headers = {'User-Agent': 'Mozilla/5.0', 'Referer': 'https://t.me/'}
                try:
                    req = urllib.request.Request(download_url, headers=headers)
                    with urllib.request.urlopen(req, timeout=300, context=ctx) as resp:
                        with open(save_path, 'wb') as f:
                            while True:
                                chunk = resp.read(8192)
                                if not chunk:
                                    break
                                f.write(chunk)
                    
                    if os.path.exists(save_path) and os.path.getsize(save_path) > 10000:
                        size_mb = os.path.getsize(save_path) / 1024 / 1024
                        log(f'   ✅ 下载成功 ({size_mb:.1f} MB)')
                        browser.close()
                        return True
                except Exception as e:
                    log(f'   直接下载失败: {e}')
            
            # 方法4: 点击触发
            log('   尝试点击下载...')
            try:
                with page.expect_download(timeout=15000) as download_info:
                    for sel in ['a.tgme_widget_message_download_button', 'a[download]', '.tgme_widget_message_document_wrap']:
                        try:
                            el = page.query_selector(sel)
                            if el:
                                el.click()
                                break
                        except:
                            continue
                download = download_info.value
                download.save_as(save_path)
                browser.close()
                if os.path.exists(save_path):
                    size_mb = os.path.getsize(save_path) / 1024 / 1024
                    log(f'   ✅ 点击下载成功 ({size_mb:.1f} MB)')
                    return True
            except Exception as e:
                log(f'   点击失败: {e}')
            
            browser.close()
            return False
    except Exception as e:
        log(f'❌ Playwright 错误: {e}')
        traceback.print_exc()
        return False


def sync_once():
    log('🚀 开始 Telegram 同步 v4')
    log(f'   频道: {", ".join(c["username"] for c in CHANNELS)}')
    log(f'   Token: {"已配置" if GITHUB_TOKEN else "未配置"}')

    all_matched = []

    for channel in CHANNELS:
        ch_name = channel['username']
        keywords = channel['keywords']
        tag = channel['tag']
        
        log(f'\n📡 频道: @{ch_name}')
        
        try:
            messages = fetch_channel_files(ch_name)
        except Exception as e:
            log(f'❌ 抓取异常: {e}')
            traceback.print_exc()
            continue
        
        for msg in messages:
            name = msg['name']
            if not match_keywords(name, keywords):
                continue
            
            date_str = datetime.now(CST).strftime('%Y-%m-%d')
            if msg['date']:
                try:
                    dt = datetime.fromisoformat(msg['date'].replace('Z', '+00:00'))
                    date_str = dt.astimezone(CST).strftime('%Y-%m-%d')
                except:
                    pass
            
            unique_id = f"{ch_name}_{name}"
            file_info = {
                'name': name, 'size': msg['size'], 'date': date_str,
                'url': msg['link'], 'download_url': '', 'direct_url': '',
                'tag': tag, 'desc': f'来自 @{ch_name}',
                'icon': '', 'file_unique_id': unique_id,
                'msg_id': msg['msg_id'], 'channel': ch_name, 'source': 'telegram'
            }
            all_matched.append(file_info)
            log(f'  ✨ {tag} {name} ({msg["size"]})')

    if all_matched:
        log(f'\n📊 共 {len(all_matched)} 个匹配，筛选最新版...')
        
        latest_by_channel = {}
        for f in all_matched:
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

        pw_ok = install_playwright()

        if pw_ok and GITHUB_TOKEN:
            release = get_or_create_release()
            if release:
                existing_assets = release.get('assets', [])
                for asset in existing_assets:
                    log(f'   删除旧: {asset["name"]}')
                    delete_release_asset(asset['id'])
                
                os.makedirs('/tmp/downloads', exist_ok=True)
                
                for f in latest_files:
                    safe_name = f['name'].replace(' ', '_')
                    local_path = f'/tmp/downloads/{f["channel"]}_{safe_name}'
                    
                    log(f'\n📥 处理: {f["name"]}')
                    success = download_file_playwright(f['url'], local_path)
                    
                    if success and os.path.exists(local_path):
                        upload_name = f'{f["channel"]}_{safe_name}'
                        dl_url = upload_file_to_release(local_path, upload_name, release)
                        if dl_url:
                            f['download_url'] = dl_url
                            f['direct_url'] = dl_url
                            log(f'   🔗 直链就绪')
                    
                    time.sleep(1)

        if GITHUB_TOKEN:
            remote_data, sha = load_remote_data()
            if remote_data is None:
                remote_data = {'files': [], 'settings': {}}

            for nf in latest_files:
                nf.pop('_msg_id_num', None)

            remote_data['files'] = latest_files
            if save_remote_data(remote_data, sha):
                log(f'\n✅ 完成！{len(latest_files)} 个文件')
                for f in latest_files:
                    has_dl = '✅ 可直下' if f.get('direct_url') else '❌ 无直链'
                    log(f'   - {f["name"]} | {has_dl}')
            else:
                log(f'❌ 保存失败')
    else:
        log('📭 没有匹配的文件')
    
    save_log_to_github()
    return len(all_matched)


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--once', action='store_true')
    args = parser.parse_args()

    try:
        sync_once()
    except Exception as e:
        log(f'❌ 致命错误: {e}')
        traceback.print_exc()
        save_log_to_github()
        sys.exit(1)


if __name__ == '__main__':
    main()
