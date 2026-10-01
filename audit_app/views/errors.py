"""Standalone branded error pages, including failures before authentication."""
from html import escape
from pathlib import Path

# Embed this small stylesheet so denied users need no authenticated asset requests.
STYLES = (Path(__file__).resolve().parent.parent / 'static' / 'errors.css').read_text()


def error_page(status, title, message, request_id='', hint=''):
    reference = (f'<p class="error-reference">Request ID <code>{escape(request_id)}</code></p>' if request_id else '')
    guidance = f'<p class="error-hint">{escape(hint)}</p>' if hint else ''
    return f'''<!doctype html><html lang="en"><head><meta charset="utf-8">
        <meta name="viewport" content="width=device-width,initial-scale=1">
        <title>{status} · {escape(title)} · AuditDesk</title><style>{STYLES}</style></head>
        <body><main class="error-shell"><a class="error-brand" href="/">Cornerstone <span>AuditDesk</span></a>
        <section class="error-card" aria-labelledby="error-title"><p class="error-code">Error {status}</p>
        <h1 id="error-title">{escape(title)}</h1><p>{escape(message)}</p>{guidance}
        <a class="error-return" href="/">Return to AuditDesk</a>{reference}</section>
        <footer>Audit Desk · Internal use only</footer></main></body></html>'''
