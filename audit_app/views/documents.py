"""Paperwork controls on an audit's result page."""
from ..database import connect
from .common import esc, local_time, retry_token


def paperwork_view(config, audit_id):
    with connect(config.database_path) as db:
        rows = db.execute('SELECT * FROM audit_documents WHERE audit_id=? ORDER BY id', (audit_id,)).fetchall()
    items = ''.join(f'<li><a href="/audits/{audit_id}/documents/{row["id"]}">{esc(row["filename"])}</a> '
                    f'· {row["page_count"]} {"page" if row["page_count"] == 1 else "pages"} · {esc(local_time(row["uploaded_at"], config.timezone))}</li>' for row in rows)
    token = retry_token(config, 'documents')
    if config.env == 'development':
        form = f'''<p>This preview accepts synthetic paperwork only.</p>
            <form method="post" action="/audits/{audit_id}/documents/sample">
            <input type="hidden" name="token" value="{token}"><button type="submit">Attach synthetic sample</button></form>'''
    else:
        form = f'''<form method="post" action="/audits/{audit_id}/documents" enctype="multipart/form-data">
            <input type="hidden" name="token" value="{token}">
            <label for="paperwork">Listing paperwork<input type="file" id="paperwork" name="document" accept=".pdf,.png,.jpg,.jpeg" required aria-describedby="paperwork-help"></label>
            <p id="paperwork-help" class="form-help">PDF, PNG or JPEG, up to 20 MiB and 100 pages per file. Upload one file at a time.</p>
            <button type="submit">Attach paperwork</button></form>'''
    return f'''<section class="paperwork" aria-labelledby="paperwork-heading"><h3 id="paperwork-heading">Paperwork</h3>
        {'<ul>' + items + '</ul>' if items else '<p>No paperwork attached yet.</p>'}{form}
        <p class="form-help">Attaching paperwork does not mark the broker response received or record a result.</p></section>'''
