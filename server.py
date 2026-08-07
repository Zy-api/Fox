#!/usr/bin/env python3
"""
DeployFlow Backend Server
- Serves the main app at /
- Provides /api/deploy endpoint to upload files and create real deployments
- Serves deployed sites at /deployed/{project-name}/
- Provides /api/send-email to send verification codes via real SMTP
- Provides /api/codes to store and retrieve verification codes
- Provides /api/tokens for API token management (real, usable tokens)
- Provides /api/sync for file synchronization with token authentication
- Provides /api/files/{project} to list deployed files
"""
import os
import json
import shutil
import hashlib
import secrets
import time
import smtplib
import ssl
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from http.server import HTTPServer, SimpleHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
import cgi
import base64

PORT = 3000
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DEPLOYED_DIR = os.path.join(BASE_DIR, 'deployed')
DATA_DIR = os.path.join(BASE_DIR, 'data')

# Data storage files
TOKENS_FILE = os.path.join(DATA_DIR, 'tokens.json')
CODES_FILE = os.path.join(DATA_DIR, 'codes.json')
DOMAINS_FILE = os.path.join(DATA_DIR, 'domains.json')

# Email configuration - users can change these
SMTP_SERVER = os.environ.get('SMTP_SERVER', '')
SMTP_PORT = int(os.environ.get('SMTP_PORT', '465'))
SMTP_USER = os.environ.get('SMTP_USER', '')
SMTP_PASS = os.environ.get('SMTP_PASS', '')
SENDER_NAME = os.environ.get('SENDER_NAME', 'DeployFlow')
SENDER_EMAIL = os.environ.get('SENDER_EMAIL', SMTP_USER)

# Ensure directories exist
os.makedirs(DEPLOYED_DIR, exist_ok=True)
os.makedirs(DATA_DIR, exist_ok=True)


def load_json(filepath, default):
    """Load JSON data from file, return default if not exists."""
    try:
        if os.path.exists(filepath):
            with open(filepath, 'r', encoding='utf-8') as f:
                return json.load(f)
    except Exception:
        pass
    return default


def save_json(filepath, data):
    """Save JSON data to file."""
    try:
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return True
    except Exception:
        return False


def generate_token():
    """Generate a real, usable API token like GitHub: dfp_ + 40 hex chars."""
    return 'dfp_' + secrets.token_hex(20)


def verify_token(token_str):
    """Verify a token string. Returns the token object if valid, None otherwise."""
    if not token_str:
        return None
    tokens = load_json(TOKENS_FILE, [])
    for t in tokens:
        if t.get('token') == token_str and t.get('active', True):
            return t
    return None


class DeployFlowHandler(SimpleHTTPRequestHandler):
    
    def do_POST(self):
        parsed = urlparse(self.path)
        
        if parsed.path == '/api/deploy':
            self.handle_deploy()
        elif parsed.path == '/api/send-email':
            self.handle_send_email()
        elif parsed.path == '/api/tokens':
            self.handle_create_token()
        elif parsed.path == '/api/sync':
            self.handle_sync()
        elif parsed.path == '/api/auto-sync':
            self.handle_auto_sync()
        elif parsed.path == '/api/update-deployed-file':
            self.handle_update_deployed_file()
        elif parsed.path == '/api/domains':
            self.handle_domain_management()
        else:
            self.send_error(404, 'Not Found')
    
    def do_DELETE(self):
        parsed = urlparse(self.path)
        
        if parsed.path.startswith('/api/tokens/'):
            token_id = parsed.path.replace('/api/tokens/', '')
            self.handle_delete_token(token_id)
        elif parsed.path.startswith('/api/domains/'):
            domain = parsed.path.replace('/api/domains/', '')
            self.handle_delete_domain(domain)
        else:
            self.send_error(404, 'Not Found')
    
    def do_GET(self):
        parsed = urlparse(self.path)
        
        # API: check deployment status
        if parsed.path.startswith('/api/status/'):
            project_name = parsed.path.replace('/api/status/', '')
            self.handle_status(project_name)
            return
        
        # API: get latest verification code
        if parsed.path == '/api/codes/latest':
            self.handle_get_latest_code()
            return
        
        # API: get all codes
        if parsed.path == '/api/codes':
            self.handle_get_codes()
            return
        
        # API: list tokens
        if parsed.path == '/api/tokens':
            self.handle_list_tokens()
            return
        
        # API: list deployed files for a project
        if parsed.path.startswith('/api/files/'):
            parts = parsed.path.replace('/api/files/', '').split('/', 1)
            project_name = parts[0]
            if len(parts) > 1:
                # API: read file content: /api/files/{project}/{filename}
                self.handle_read_file(project_name, parts[1])
            else:
                self.handle_list_files(project_name)
            return
        
        # API: get project timestamp for auto-refresh
        if parsed.path.startswith('/api/timestamp/'):
            project_name = parsed.path.replace('/api/timestamp/', '')
            self.handle_timestamp(project_name)
            return
        
        # Serve deployed sites
        if parsed.path.startswith('/deployed/'):
            self.serve_deployed(parsed.path)
            return
        
        # Custom domain routing: check if Host header matches a bound domain
        host = self.headers.get('Host', '').split(':')[0]  # Remove port if present
        domains = load_json(DOMAINS_FILE, {})
        if host in domains:
            project_name = domains[host].get('project', '')
            if project_name:
                # Serve from the project's deployed directory
                safe_name = ''.join(c for c in project_name if c.isalnum() or c in '-_').lower()
                deploy_dir = os.path.join(DEPLOYED_DIR, safe_name)
                if os.path.exists(deploy_dir):
                    # Map the request path to the deployed directory
                    rel_path = parsed.path.lstrip('/')
                    if not rel_path:
                        rel_path = 'index.html'
                    full_path = os.path.join(deploy_dir, rel_path)
                    if os.path.isdir(full_path):
                        full_path = os.path.join(full_path, 'index.html')
                    if os.path.isfile(full_path):
                        self.serve_file(full_path, safe_name)
                        return
                    self.send_error(404, 'Not Found')
                    return
        
        # Default: serve static files
        super().do_GET()
    
    def handle_send_email(self):
        """Send verification code email via SMTP and store it for retrieval"""
        content_length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(content_length)
        try:
            data = json.loads(body)
            to_email = data.get('email', '').strip()
            code = data.get('code', '').strip()
            
            if not to_email or not code:
                self.send_json(400, {'error': '邮箱和验证码不能为空'})
                return
            
            # Store the code for retrieval by the verification receiver page
            codes = load_json(CODES_FILE, [])
            code_entry = {
                'code': code,
                'email': to_email,
                'timestamp': int(time.time()),
                'id': 'code_' + str(int(time.time() * 1000))
            }
            codes.append(code_entry)
            # Keep only the latest 50 codes
            if len(codes) > 50:
                codes = codes[-50:]
            save_json(CODES_FILE, codes)
            
            # If SMTP is configured, send real email
            if SMTP_SERVER and SMTP_USER and SMTP_PASS:
                try:
                    msg = MIMEMultipart('alternative')
                    msg['From'] = f'{SENDER_NAME} <{SENDER_EMAIL}>'
                    msg['To'] = to_email
                    msg['Subject'] = f'【DeployFlow】您的验证码：{code}'
                    
                    html_content = f'''<!DOCTYPE html>
<html><body style="font-family:sans-serif;max-width:480px;margin:0 auto;padding:20px">
<div style="background:#f5f5f5;border-radius:12px;padding:32px">
<h2 style="color:#4d8bf5;margin:0 0 16px">DeployFlow 验证码</h2>
<p style="color:#333;font-size:14px">您正在注册 DeployFlow 账号，验证码为：</p>
<div style="text-align:center;margin:24px 0">
<span style="font-size:32px;font-weight:700;letter-spacing:6px;color:#4d8bf5">{code}</span>
</div>
<p style="color:#666;font-size:13px">验证码 5 分钟内有效，请尽快使用。</p>
<p style="color:#999;font-size:12px;margin-top:24px">如果不是您本人操作，请忽略此邮件。</p>
</div></body></html>'''
                    
                    text_content = f'DeployFlow 验证码：{code}\n\n验证码 5 分钟内有效，请尽快使用。\n如果不是您本人操作，请忽略此邮件。'
                    
                    msg.attach(MIMEText(text_content, 'plain', 'utf-8'))
                    msg.attach(MIMEText(html_content, 'html', 'utf-8'))
                    
                    context = ssl.create_default_context()
                    if SMTP_PORT == 465:
                        with smtplib.SMTP_SSL(SMTP_SERVER, SMTP_PORT, context=context) as server:
                            server.login(SMTP_USER, SMTP_PASS)
                            server.sendmail(SENDER_EMAIL, to_email, msg.as_string())
                    else:
                        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
                            server.starttls(context=context)
                            server.login(SMTP_USER, SMTP_PASS)
                            server.sendmail(SENDER_EMAIL, to_email, msg.as_string())
                    
                    self.send_json(200, {'success': True, 'message': '邮件已发送至 ' + to_email, 'code_id': code_entry['id']})
                except Exception as e:
                    self.send_json(200, {'success': True, 'simulated': True, 'code': code, 'code_id': code_entry['id'], 'message': '验证码已存储，可在验证码接收页查看'})
            else:
                # SMTP not configured - code is stored, return it so frontend can display it
                self.send_json(200, {
                    'success': True,
                    'simulated': True,
                    'code': code,
                    'code_id': code_entry['id'],
                    'message': '验证码已存储，可在验证码接收页查看'
                })
        except Exception as e:
            self.send_json(500, {'error': str(e)})
    
    def handle_get_latest_code(self):
        """Return the most recent verification code"""
        codes = load_json(CODES_FILE, [])
        if not codes:
            self.send_json(200, {'code': None, 'message': '暂无验证码'})
            return
        latest = codes[-1]
        # Check if code is within 5 minutes
        age = int(time.time()) - latest.get('timestamp', 0)
        latest['age_seconds'] = age
        latest['expired'] = age > 300
        self.send_json(200, latest)
    
    def handle_get_codes(self):
        """Return all verification codes"""
        codes = load_json(CODES_FILE, [])
        now = int(time.time())
        for c in codes:
            c['age_seconds'] = now - c.get('timestamp', 0)
            c['expired'] = c['age_seconds'] > 300
        # Return in reverse order (newest first)
        self.send_json(200, {'codes': list(reversed(codes)), 'total': len(codes)})
    
    def handle_create_token(self):
        """Create a new API token"""
        content_length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(content_length)
        try:
            data = json.loads(body)
            name = data.get('name', '').strip()
            scope = data.get('scope', 'deploy')  # deploy, sync, full
            
            # Name is optional - auto-generate if empty
            if not name:
                name = 'Token_' + str(int(time.time()))[-6:]
            
            tokens = load_json(TOKENS_FILE, [])
            token_str = generate_token()
            token_obj = {
                'id': 'tok_' + str(int(time.time() * 1000)),
                'name': name,
                'token': token_str,
                'scope': scope,
                'active': True,
                'created_at': int(time.time()),
                'last_used': None,
                'usage_count': 0
            }
            tokens.append(token_obj)
            save_json(TOKENS_FILE, tokens)
            
            self.send_json(200, {
                'success': True,
                'token': token_str,
                'id': token_obj['id'],
                'name': name,
                'scope': scope,
                'message': 'Token 已创建，请妥善保存。Token 仅在创建时显示一次。'
            })
        except Exception as e:
            self.send_json(500, {'error': str(e)})
    
    def handle_list_tokens(self):
        """List all tokens (without showing the full token string)"""
        tokens = load_json(TOKENS_FILE, [])
        # Mask the token for security, only show first 8 and last 4 chars
        safe_tokens = []
        for t in tokens:
            token_str = t.get('token', '')
            masked = token_str[:8] + '****' + token_str[-4:] if len(token_str) > 12 else '****'
            safe_t = {
                'id': t.get('id'),
                'name': t.get('name'),
                'token_masked': masked,
                'scope': t.get('scope', 'deploy'),
                'active': t.get('active', True),
                'created_at': t.get('created_at'),
                'last_used': t.get('last_used'),
                'usage_count': t.get('usage_count', 0)
            }
            safe_tokens.append(safe_t)
        self.send_json(200, {'tokens': safe_tokens})
    
    def handle_delete_token(self, token_id):
        """Delete/revoke a token by ID"""
        tokens = load_json(TOKENS_FILE, [])
        new_tokens = [t for t in tokens if t.get('id') != token_id]
        if len(new_tokens) == len(tokens):
            self.send_json(404, {'error': 'Token 不存在'})
            return
        save_json(TOKENS_FILE, new_tokens)
        self.send_json(200, {'success': True, 'message': 'Token 已删除'})
    
    def handle_sync(self):
        """Sync files to a project using token authentication"""
        # Check Authorization header
        auth_header = self.headers.get('Authorization', '')
        token_str = ''
        if auth_header.startswith('Bearer '):
            token_str = auth_header[7:]
        elif auth_header.startswith('token '):
            token_str = auth_header[6:]
        
        token_obj = verify_token(token_str)
        if not token_obj:
            self.send_json(401, {'error': '无效的 Token 或 Token 已被撤销'})
            return
        
        # Update token usage
        tokens = load_json(TOKENS_FILE, [])
        for t in tokens:
            if t.get('id') == token_obj.get('id'):
                t['last_used'] = int(time.time())
                t['usage_count'] = t.get('usage_count', 0) + 1
                break
        save_json(TOKENS_FILE, tokens)
        
        content_length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(content_length)
        try:
            data = json.loads(body)
            project_name = data.get('project', '').strip()
            files = data.get('files', [])
            mode = data.get('mode', 'merge')  # merge or replace
            
            if not project_name:
                self.send_json(400, {'error': '项目名称不能为空'})
                return
            
            if not files:
                self.send_json(400, {'error': '没有文件需要同步'})
                return
            
            safe_name = ''.join(c for c in project_name if c.isalnum() or c in '-_').lower()
            if not safe_name:
                safe_name = hashlib.md5(project_name.encode()).hexdigest()[:8]
            
            deploy_dir = os.path.join(DEPLOYED_DIR, safe_name)
            
            if mode == 'replace':
                # Clear existing files
                if os.path.exists(deploy_dir):
                    shutil.rmtree(deploy_dir)
            os.makedirs(deploy_dir, exist_ok=True)
            
            # Write files
            written = []
            for f in files:
                fname = f.get('name', 'unknown')
                fcontent = f.get('content', '')
                try:
                    fbytes = base64.b64decode(fcontent)
                except:
                    fbytes = fcontent.encode()
                
                fpath = os.path.join(deploy_dir, fname)
                os.makedirs(os.path.dirname(fpath) if os.path.dirname(fpath) else deploy_dir, exist_ok=True)
                with open(fpath, 'wb') as fp:
                    fp.write(fbytes)
                written.append(fname)
            
            # Create index.html if not exists
            index_path = os.path.join(deploy_dir, 'index.html')
            if not os.path.exists(index_path):
                html_files = []
                for root, dirs, fnames in os.walk(deploy_dir):
                    for fn in fnames:
                        if fn.lower().endswith('.html'):
                            rel = os.path.relpath(os.path.join(root, fn), deploy_dir)
                            html_files.append(rel)
                if html_files:
                    with open(index_path, 'w', encoding='utf-8') as fp:
                        fp.write(self.file_listing_index(project_name, html_files))
                else:
                    with open(index_path, 'w', encoding='utf-8') as fp:
                        fp.write(self.default_index(project_name))
            
            host = self.headers.get('Host', f'localhost:{PORT}')
            base_url = f'http://{host}'
            deploy_url = f'{base_url}/deployed/{safe_name}/'
            
            self.send_json(200, {
                'success': True,
                'url': deploy_url,
                'project': safe_name,
                'files_synced': len(written),
                'files': written,
                'mode': mode,
                'message': f'同步成功，{len(written)} 个文件已更新'
            })
        except Exception as e:
            self.send_json(500, {'error': str(e)})
    
    def handle_list_files(self, project_name):
        """List all deployed files for a project"""
        safe_name = ''.join(c for c in project_name if c.isalnum() or c in '-_').lower()
        deploy_dir = os.path.join(DEPLOYED_DIR, safe_name)
        if not os.path.exists(deploy_dir):
            self.send_json(404, {'error': '项目不存在'})
            return
        
        files = []
        for root, dirs, fnames in os.walk(deploy_dir):
            for fn in fnames:
                full = os.path.join(root, fn)
                rel = os.path.relpath(full, deploy_dir)
                size = os.path.getsize(full)
                ext = fn.split('.')[-1].lower() if '.' in fn else ''
                files.append({'name': rel, 'size': size, 'ext': ext})
        
        files.sort(key=lambda x: x['name'])
        host = self.headers.get('Host', f'localhost:{PORT}')
        base_url = f'http://{host}'
        
        self.send_json(200, {
            'project': safe_name,
            'url': f'{base_url}/deployed/{safe_name}/',
            'files': files,
            'total': len(files)
        })
    
    def handle_read_file(self, project_name, filename):
        """Read content of a deployed file for editing"""
        safe_name = ''.join(c for c in project_name if c.isalnum() or c in '-_').lower()
        deploy_dir = os.path.join(DEPLOYED_DIR, safe_name)
        file_path = os.path.join(deploy_dir, filename)
        
        # Security: ensure path is within deploy_dir
        if not os.path.realpath(file_path).startswith(os.path.realpath(deploy_dir)):
            self.send_json(403, {'error': '路径不允许'})
            return
        
        if not os.path.exists(file_path) or not os.path.isfile(file_path):
            self.send_json(404, {'error': '文件不存在'})
            return
        
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
            self.send_json(200, {
                'success': True,
                'project': safe_name,
                'filename': filename,
                'content': content
            })
        except Exception as e:
            # For binary files, return base64
            try:
                import base64
                with open(file_path, 'rb') as f:
                    b64 = base64.b64encode(f.read()).decode()
                self.send_json(200, {
                    'success': True,
                    'project': safe_name,
                    'filename': filename,
                    'content': '',
                    'binary': True,
                    'base64': b64
                })
            except Exception as e2:
                self.send_json(500, {'error': str(e2)})
    
    def handle_auto_sync(self):
        """Auto-sync: save edited file content directly to deployed directory (no token needed)"""
        content_length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(content_length)
        try:
            data = json.loads(body)
            project_name = data.get('project', '').strip()
            filename = data.get('filename', '').strip()
            content = data.get('content', '')
            
            if not project_name or not filename:
                self.send_json(400, {'error': '项目名称和文件名不能为空'})
                return
            
            safe_name = ''.join(c for c in project_name if c.isalnum() or c in '-_').lower()
            if not safe_name:
                safe_name = hashlib.md5(project_name.encode()).hexdigest()[:8]
            
            deploy_dir = os.path.join(DEPLOYED_DIR, safe_name)
            file_path = os.path.join(deploy_dir, filename)
            
            # Security: ensure path is within deploy_dir
            if not os.path.realpath(os.path.dirname(file_path)).startswith(os.path.realpath(deploy_dir)):
                self.send_json(403, {'error': '路径不允许'})
                return
            
            os.makedirs(os.path.dirname(file_path) if os.path.dirname(file_path) else deploy_dir, exist_ok=True)
            
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(content)
            
            host = self.headers.get('Host', f'localhost:{PORT}')
            file_url = f'http://{host}/deployed/{safe_name}/{filename}'
            
            self.send_json(200, {
                'success': True,
                'project': safe_name,
                'filename': filename,
                'url': file_url,
                'message': f'{filename} 已自动同步，前台将在 3 秒内自动刷新'
            })
        except Exception as e:
            self.send_json(500, {'error': str(e)})
    
    def handle_update_deployed_file(self):
        """Allow deployed websites (e.g. admin.html) to write file changes back to the server.
        The deployed project is identified by the Referer header (which contains /deployed/{project}/)."""
        content_length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(content_length)
        try:
            data = json.loads(body)
            project_name = data.get('project', '').strip()
            filename = data.get('filename', '').strip()
            content = data.get('content', '')
            
            # If project not specified, try to extract from Referer header
            if not project_name:
                referer = self.headers.get('Referer', '')
                if '/deployed/' in referer:
                    ref_path = referer.split('/deployed/')[1].split('/')[0].split('?')[0]
                    project_name = ref_path
            
            if not project_name or not filename:
                self.send_json(400, {'error': '项目名称和文件名不能为空'})
                return
            
            safe_name = ''.join(c for c in project_name if c.isalnum() or c in '-_').lower()
            if not safe_name:
                safe_name = hashlib.md5(project_name.encode()).hexdigest()[:8]
            
            deploy_dir = os.path.join(DEPLOYED_DIR, safe_name)
            file_path = os.path.join(deploy_dir, filename)
            
            # Security: ensure path is within deploy_dir
            if not os.path.realpath(os.path.dirname(file_path)).startswith(os.path.realpath(deploy_dir)):
                self.send_json(403, {'error': '路径不允许'})
                return
            
            os.makedirs(os.path.dirname(file_path) if os.path.dirname(file_path) else deploy_dir, exist_ok=True)
            
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(content)
            
            host = self.headers.get('Host', f'localhost:{PORT}')
            file_url = f'http://{host}/deployed/{safe_name}/{filename}'
            
            self.send_json(200, {
                'success': True,
                'project': safe_name,
                'filename': filename,
                'url': file_url,
                'message': f'{filename} 已保存，前台将在 3 秒内自动刷新'
            })
        except Exception as e:
            self.send_json(500, {'error': str(e)})
    
    def handle_timestamp(self, project_name):
        """Return the latest file modification timestamp for a project (for auto-refresh)"""
        safe_name = ''.join(c for c in project_name if c.isalnum() or c in '-_').lower()
        if not safe_name:
            safe_name = hashlib.md5(project_name.encode()).hexdigest()[:8]
        deploy_dir = os.path.join(DEPLOYED_DIR, safe_name)
        if not os.path.exists(deploy_dir):
            self.send_json(404, {'error': '项目不存在'})
            return
        latest_mtime = 0
        for root, dirs, files in os.walk(deploy_dir):
            for f in files:
                full = os.path.join(root, f)
                mtime = os.path.getmtime(full)
                if mtime > latest_mtime:
                    latest_mtime = mtime
        self.send_json(200, {'project': safe_name, 'timestamp': int(latest_mtime * 1000)})
    
    def handle_domain_management(self):
        """Bind or list custom domains"""
        if self.command == 'GET':
            domains = load_json(DOMAINS_FILE, {})
            self.send_json(200, {'domains': domains})
            return
        
        # POST: bind a domain
        content_length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(content_length)
        try:
            data = json.loads(body)
            action = data.get('action', 'bind')
            domain = data.get('domain', '').strip().lower()
            project_name = data.get('project', '').strip()
            
            if not domain:
                self.send_json(400, {'error': '域名不能为空'})
                return
            
            # Validate domain format
            import re
            if not re.match(r'^([a-z0-9]([a-z0-9-]*[a-z0-9])?\.)+[a-z]{2,}$', domain):
                self.send_json(400, {'error': '域名格式不正确，例如：my-site.com'})
                return
            
            domains = load_json(DOMAINS_FILE, {})
            
            if action == 'unbind':
                if domain in domains:
                    del domains[domain]
                    save_json(DOMAINS_FILE, domains)
                    self.send_json(200, {'success': True, 'message': f'域名 {domain} 已解绑'})
                else:
                    self.send_json(404, {'error': '域名未绑定'})
                return
            
            if not project_name:
                self.send_json(400, {'error': '项目名称不能为空'})
                return
            
            # Check if domain is already bound to another project
            if domain in domains and domains[domain].get('project') != project_name:
                self.send_json(400, {'error': f'域名 {domain} 已被其他项目绑定'})
                return
            
            # Check if project exists
            safe_name = ''.join(c for c in project_name if c.isalnum() or c in '-_').lower()
            deploy_dir = os.path.join(DEPLOYED_DIR, safe_name)
            if not os.path.exists(deploy_dir):
                self.send_json(400, {'error': '项目不存在，请先部署'})
                return
            
            host = self.headers.get('Host', f'localhost:{PORT}').split(':')[0]
            server_ip = self.headers.get('Host', f'localhost:{PORT}')
            
            domains[domain] = {
                'project': project_name,
                'safe_name': safe_name,
                'bound_at': int(time.time())
            }
            save_json(DOMAINS_FILE, domains)
            
            self.send_json(200, {
                'success': True,
                'domain': domain,
                'project': project_name,
                'message': f'域名 {domain} 已绑定到项目 {project_name}',
                'dns_instructions': f'请在你的域名 DNS 设置中添加 A 记录：\n类型: A\n主机记录: @\n记录值: {server_ip.split(":")[0]}\n\n或添加 CNAME 记录：\n类型: CNAME\n主机记录: @\n记录值: {server_ip.split(":")[0]}',
                'note': 'DNS 生效后（通常几分钟到几小时），通过域名即可直接访问你的网站'
            })
        except Exception as e:
            self.send_json(500, {'error': str(e)})
    
    def handle_delete_domain(self, domain):
        """Unbind a custom domain"""
        domains = load_json(DOMAINS_FILE, {})
        if domain in domains:
            del domains[domain]
            save_json(DOMAINS_FILE, domains)
            self.send_json(200, {'success': True, 'message': f'域名 {domain} 已解绑'})
        else:
            self.send_json(404, {'error': '域名未绑定'})
    
    def handle_deploy(self):
        content_type = self.headers.get('Content-Type', '')
        
        if 'multipart/form-data' not in content_type:
            # JSON body with file data
            content_length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(content_length)
            try:
                data = json.loads(body)
                project_name = data.get('name', '').strip()
                files = data.get('files', [])
                
                if not project_name:
                    self.send_json(400, {'error': '项目名称不能为空'})
                    return
                
                # Sanitize project name
                safe_name = ''.join(c for c in project_name if c.isalnum() or c in '-_').lower()
                if not safe_name:
                    safe_name = hashlib.md5(project_name.encode()).hexdigest()[:8]
                
                deploy_dir = os.path.join(DEPLOYED_DIR, safe_name)
                os.makedirs(deploy_dir, exist_ok=True)
                
                # Write files
                for f in files:
                    fname = f.get('name', 'unknown')
                    fcontent = f.get('content', '')
                    # Decode base64 content
                    import base64
                    try:
                        fbytes = base64.b64decode(fcontent)
                    except:
                        fbytes = fcontent.encode()
                    
                    # Create subdirectories if needed
                    fpath = os.path.join(deploy_dir, fname)
                    os.makedirs(os.path.dirname(fpath), exist_ok=True)
                    with open(fpath, 'wb') as fp:
                        fp.write(fbytes)
                
                # Create index.html if not exists - list all HTML files if available
                index_path = os.path.join(deploy_dir, 'index.html')
                if not os.path.exists(index_path):
                    # Find all HTML files in the deployment
                    html_files = []
                    for root, dirs, fnames in os.walk(deploy_dir):
                        for fn in fnames:
                            if fn.lower().endswith('.html'):
                                rel = os.path.relpath(os.path.join(root, fn), deploy_dir)
                                html_files.append(rel)
                    
                    if html_files:
                        # Create an index page that links to all HTML files
                        with open(index_path, 'w', encoding='utf-8') as fp:
                            fp.write(self.file_listing_index(project_name, html_files))
                    else:
                        with open(index_path, 'w', encoding='utf-8') as fp:
                            fp.write(self.default_index(project_name))
                
                # Build URL based on the Host header so it works from any device
                host = self.headers.get('Host', f'localhost:{PORT}')
                base_url = f'http://{host}'
                deploy_url = f'{base_url}/deployed/{safe_name}/'
                
                self.send_json(200, {
                    'success': True,
                    'url': deploy_url,
                    'name': safe_name,
                    'files': len(files)
                })
            except Exception as e:
                self.send_json(500, {'error': str(e)})
        else:
            # Multipart form data
            form = cgi.FieldStorage(
                fp=self.rfile,
                headers=self.headers,
                environ={'REQUEST_METHOD': 'POST', 'CONTENT_TYPE': content_type}
            )
            
            project_name = form.getvalue('name', '').strip()
            if not project_name:
                self.send_json(400, {'error': '项目名称不能为空'})
                return
            
            safe_name = ''.join(c for c in project_name if c.isalnum() or c in '-_').lower()
            if not safe_name:
                safe_name = hashlib.md5(project_name.encode()).hexdigest()[:8]
            
            deploy_dir = os.path.join(DEPLOYED_DIR, safe_name)
            os.makedirs(deploy_dir, exist_ok=True)
            
            file_count = 0
            # Handle multiple files
            if 'files' in form:
                items = form['files']
                if not isinstance(items, list):
                    items = [items]
                for item in items:
                    if item.filename:
                        fpath = os.path.join(deploy_dir, item.filename)
                        os.makedirs(os.path.dirname(fpath), exist_ok=True)
                        with open(fpath, 'wb') as fp:
                            fp.write(item.file.read())
                        file_count += 1
            
            # Create index.html if not exists - list all HTML files if available
            index_path = os.path.join(deploy_dir, 'index.html')
            if not os.path.exists(index_path):
                html_files = []
                for root, dirs, fnames in os.walk(deploy_dir):
                    for fn in fnames:
                        if fn.lower().endswith('.html'):
                            rel = os.path.relpath(os.path.join(root, fn), deploy_dir)
                            html_files.append(rel)
                
                if html_files:
                    with open(index_path, 'w', encoding='utf-8') as fp:
                        fp.write(self.file_listing_index(project_name, html_files))
                else:
                    with open(index_path, 'w', encoding='utf-8') as fp:
                        fp.write(self.default_index(project_name))
            
            host = self.headers.get('Host', f'localhost:{PORT}')
            base_url = f'http://{host}'
            deploy_url = f'{base_url}/deployed/{safe_name}/'
            
            self.send_json(200, {
                'success': True,
                'url': deploy_url,
                'name': safe_name,
                'files': file_count
            })
    
    def handle_status(self, project_name):
        safe_name = ''.join(c for c in project_name if c.isalnum() or c in '-_').lower()
        deploy_dir = os.path.join(DEPLOYED_DIR, safe_name)
        if os.path.exists(deploy_dir):
            files = os.listdir(deploy_dir)
            self.send_json(200, {'deployed': True, 'files': files})
        else:
            self.send_json(200, {'deployed': False})
    
    def serve_deployed(self, path):
        # Remove /deployed/ prefix
        rel_path = path[len('/deployed/'):]
        # Strip query string if any
        if '?' in rel_path:
            rel_path = rel_path.split('?')[0]
        full_path = os.path.join(DEPLOYED_DIR, rel_path)
        
        # Security: prevent path traversal
        if not os.path.realpath(full_path).startswith(os.path.realpath(DEPLOYED_DIR)):
            self.send_error(403, 'Forbidden')
            return
        
        if os.path.isdir(full_path):
            # Serve index.html from directory
            index_file = os.path.join(full_path, 'index.html')
            if os.path.exists(index_file):
                full_path = index_file
            else:
                # No index.html - check for other HTML files and create a listing
                self.send_html_listing(full_path, rel_path)
                return
        
        if os.path.isfile(full_path):
            # Extract project name for auto-refresh injection
            parts = rel_path.split('/')
            project_name = parts[0] if parts else ''
            self.serve_file(full_path, project_name)
        else:
            self.send_error(404, 'Not Found')
    
    def serve_file(self, filepath, project_name=None):
        ext = os.path.splitext(filepath)[1].lower()
        content_types = {
            '.html': 'text/html; charset=utf-8',
            '.css': 'text/css; charset=utf-8',
            '.js': 'application/javascript; charset=utf-8',
            '.json': 'application/json; charset=utf-8',
            '.png': 'image/png',
            '.jpg': 'image/jpeg',
            '.jpeg': 'image/jpeg',
            '.gif': 'image/gif',
            '.svg': 'image/svg+xml',
            '.ico': 'image/x-icon',
            '.woff': 'font/woff',
            '.woff2': 'font/woff2',
            '.ttf': 'font/ttf',
            '.txt': 'text/plain; charset=utf-8',
            '.xml': 'text/xml; charset=utf-8',
            '.pdf': 'application/pdf',
            '.mp4': 'video/mp4',
            '.webp': 'image/webp',
        }
        
        ctype = content_types.get(ext, 'application/octet-stream')
        
        try:
            with open(filepath, 'rb') as f:
                content = f.read()
            
            # Inject auto-refresh + DeployFlow SDK into HTML files served from deployed directory
            if ext == '.html' and project_name:
                safe_name = ''.join(c for c in project_name if c.isalnum() or c in '-_').lower()
                if not safe_name:
                    safe_name = hashlib.md5(project_name.encode()).hexdigest()[:8]
                injected_script = b'''<script>(function(){
var P="''' + safe_name.encode() + b'''";
var T=0;
window.DeployFlow={
  project:P,
  saveFile:function(fn,ct,cb){
    fetch("/api/update-deployed-file",{method:"POST",headers:{"Content-Type":"application/json"},
    body:JSON.stringify({project:P,filename:fn,content:ct})})
    .then(function(r){return r.json()})
    .then(function(d){if(cb)cb(d)})
    .catch(function(e){if(cb)cb({success:false,error:String(e)})})
  },
  readFile:function(fn,cb){
    fetch("/api/files/"+P+"/"+fn)
    .then(function(r){return r.json()})
    .then(function(d){if(cb)cb(d)})
    .catch(function(e){if(cb)cb({success:false,error:String(e)})})
  }
};
fetch("/api/timestamp/"+P).then(function(r){return r.json()}).then(function(d){T=d.timestamp||0});
setInterval(function(){
  fetch("/api/timestamp/"+P).then(function(r){return r.json()}).then(function(d){
    if(T&&d.timestamp&&d.timestamp>T){location.reload()}
  }).catch(function(){})
},3000)
})()</script>'''
                html_content = content
                # Try to inject before </body>
                body_close = html_content.rfind(b'</body>')
                if body_close != -1:
                    html_content = html_content[:body_close] + injected_script + html_content[body_close:]
                else:
                    html_content = html_content + injected_script
                content = html_content
            
            self.send_response(200)
            self.send_header('Content-Type', ctype)
            self.send_header('Content-Length', len(content))
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(content)
        except Exception as e:
            self.send_error(500, str(e))
    
    def send_html_listing(self, dirpath, relpath):
        """Generate a user-friendly listing of deployed files, highlighting HTML files."""
        all_files = []
        for root, dirs, files in os.walk(dirpath):
            for f in files:
                full = os.path.join(root, f)
                rel = os.path.relpath(full, dirpath)
                all_files.append(rel)
        all_files.sort()
        
        html_files = [f for f in all_files if f.lower().endswith('.html')]
        other_files = [f for f in all_files if not f.lower().endswith('.html')]
        
        # Build HTML
        parts = []
        parts.append('<!DOCTYPE html>')
        parts.append('<html lang="zh-CN"><head><meta charset="UTF-8">')
        parts.append('<meta name="viewport" content="width=device-width,initial-scale=1.0">')
        parts.append(f'<title>已部署文件 - {relpath.rstrip("/")}</title>')
        parts.append('<style>')
        parts.append('body{font-family:-apple-system,sans-serif;max-width:720px;margin:0 auto;padding:40px 20px;background:#f5f5f5}')
        parts.append('h1{font-size:22px;color:#333;margin-bottom:4px}')
        parts.append('.count{color:#666;font-size:13px;margin-bottom:24px}')
        parts.append('.section{font-size:14px;font-weight:600;color:#333;margin:20px 0 8px}')
        parts.append('a{color:#4d8bf5;text-decoration:none;font-size:14px}')
        parts.append('a:hover{text-decoration:underline}')
        parts.append('.file{display:flex;align-items:center;gap:10px;padding:8px 12px;background:#fff;border-radius:6px;margin-bottom:4px}')
        parts.append('.tag{font-size:10px;font-weight:700;padding:2px 6px;border-radius:3px;color:#fff}')
        parts.append('.tag-html{background:#4d8bf5}')
        parts.append('.tag-css{background:#22c55e}')
        parts.append('.tag-js{background:#f59e0b}')
        parts.append('.tag-img{background:#a855f7}')
        parts.append('.tag-other{background:#999}')
        parts.append('.path{color:#999;font-size:12px}')
        parts.append('.footer{text-align:center;color:#999;font-size:12px;margin-top:32px}')
        parts.append('</style></head><body>')
        parts.append(f'<h1>{relpath.rstrip("/")}</h1>')
        parts.append(f'<div class="count">共 {len(all_files)} 个文件（{len(html_files)} 个 HTML 页面）</div>')
        
        if html_files:
            parts.append('<div class="section">HTML 页面</div>')
            for f in html_files:
                name = f.split('/')[-1]
                parts.append(f'<div class="file"><span class="tag tag-html">HTML</span><a href="{f}">{name}</a><span class="path">{f}</span></div>')
        
        if other_files:
            parts.append('<div class="section">其他文件</div>')
            for f in other_files:
                ext = f.split('.')[-1].lower() if '.' in f else ''
                name = f.split('/')[-1]
                tag_class = 'tag-other'
                tag_label = ext.upper()[:4] if ext else 'FILE'
                if ext in ('css',): tag_class = 'tag-css'; tag_label = 'CSS'
                elif ext in ('js',): tag_class = 'tag-js'; tag_label = 'JS'
                elif ext in ('png','jpg','jpeg','gif','svg','webp','ico'): tag_class = 'tag-img'; tag_label = 'IMG'
                parts.append(f'<div class="file"><span class="tag {tag_class}">{tag_label}</span><a href="{f}">{name}</a><span class="path">{f}</span></div>')
        
        parts.append('<div class="footer">Powered by DeployFlow</div>')
        parts.append('</body></html>')
        
        html = '\n'.join(parts)
        content = html.encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', len(content))
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(content)
    
    def default_index(self, name):
        return f'''<!DOCTYPE html>
<html lang="zh-CN">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>{name}</title></head>
<body style="font-family:sans-serif;display:flex;align-items:center;justify-content:center;min-height:100vh;margin:0;background:#0a0a0a;color:#fff">
<div style="text-align:center">
<h1 style="font-size:32px;margin-bottom:8px">{name}</h1>
<p style="color:#888;font-size:14px">已通过 DeployFlow 部署成功</p>
<p style="color:#555;font-size:12px;margin-top:16px">Powered by DeployFlow</p>
</div></body></html>'''
    
    def file_listing_index(self, name, html_files):
        """Generate an index.html that lists all HTML files as links."""
        links = []
        for f in sorted(html_files):
            display = f.split('/')[-1]
            links.append(f'<li style="margin:10px 0"><a href="{f}" style="color:#4d8bf5;font-size:18px;text-decoration:none;font-weight:500">{display}</a><span style="color:#999;font-size:13px;margin-left:12px">{f}</span></li>')
        links_html = '\n'.join(links)
        return f'''<!DOCTYPE html>
<html lang="zh-CN">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>{name} - 文件列表</title></head>
<body style="font-family:-apple-system,sans-serif;max-width:680px;margin:0 auto;padding:40px 20px;background:#f5f5f5">
<h1 style="font-size:24px;color:#333;margin-bottom:4px">{name}</h1>
<p style="color:#666;font-size:14px;margin-bottom:24px">从仓库部署，共 {len(html_files)} 个 HTML 页面</p>
<ul style="list-style:none;padding:0;background:#fff;border-radius:8px;padding:24px;box-shadow:0 1px 3px rgba(0,0,0,.08)">
{links_html}
</ul>
<p style="color:#999;font-size:12px;margin-top:24px;text-align:center">Powered by DeployFlow</p>
</body></html>'''
    
    def send_json(self, code, data):
        content = json.dumps(data, ensure_ascii=False).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', len(content))
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, DELETE, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type, Authorization')
        self.end_headers()
        self.wfile.write(content)
    
    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, DELETE, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type, Authorization')
        self.end_headers()
    
    def log_message(self, format, *args):
        # Suppress log messages for cleaner output
        pass


if __name__ == '__main__':
    server = HTTPServer(('0.0.0.0', PORT), DeployFlowHandler)
    print(f'DeployFlow server running at http://localhost:{PORT}')
    print(f'Deployed sites: http://localhost:{PORT}/deployed/')
    server.serve_forever()
