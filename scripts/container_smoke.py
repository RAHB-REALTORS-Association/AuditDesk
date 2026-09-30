"""Exercise a built image with disposable storage and no integration credentials."""
import json
import os
import secrets
import subprocess
import sys
import time

image = sys.argv[1] if len(sys.argv)>1 else 'auditdesk:foundation'
name = 'auditdesk-smoke-' + secrets.token_hex(4)
volume = name + '-data'
env = {**os.environ, 'APP_SECRET_KEY':secrets.token_urlsafe(48)}
def docker(*args, capture=True):
    return subprocess.check_output(['docker',*args],env=env,text=True,stderr=subprocess.PIPE).strip()
def ready():
    for _ in range(40):
        try:
            result=docker('exec',name,'python','-c',"import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8765/healthz',timeout=2).read().decode())")
            if json.loads(result)['status']=='ok':return
        except (subprocess.CalledProcessError,ValueError):pass
        time.sleep(1)
    raise RuntimeError('Container did not become ready')
try:
    docker('volume','create',volume)
    docker('run','-d','--name',name,'--mount',f'type=volume,source={volume},target=/app/data',
           '-e','APP_SECRET_KEY','-e','APP_ENV=test','-e','PUBLIC_BASE_URL=https://audit.example.com',
           '-e','CF_ACCESS_ISSUER=https://example.cloudflareaccess.com','-e','CF_ACCESS_AUDIENCE=smoke-only',
           '-e','BOOTSTRAP_ADMIN_EMAILS=smoke@example.com',image)
    ready()
    assert docker('exec',name,'id','-u')=='10001'
    # Origin bypass must fail without a signed Access identity.
    denied=docker('exec',name,'python','-c',"import urllib.request,urllib.error\ntry: urllib.request.urlopen('http://127.0.0.1:8765/')\nexcept urllib.error.HTTPError as e: print(e.code)")
    assert denied=='401',denied
    docker('exec',name,'python','-c',"import sqlite3; db=sqlite3.connect('/app/data/audit.sqlite3'); db.execute(\"UPDATE app_users SET display_name='Survives restart'\"); db.commit(); db.close()")
    docker('restart',name)
    ready()
    assert docker('exec',name,'python','-c',"import sqlite3; db=sqlite3.connect('/app/data/audit.sqlite3'); print(db.execute('SELECT display_name FROM app_users').fetchone()[0]); db.close()")=='Survives restart'
    print('PASS: non-root startup, health, authentication denial, and persistent data after restart')
finally:
    subprocess.run(['docker','rm','-f',name],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    subprocess.run(['docker','volume','rm',volume],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
