"""Manual failed-audit handoff; no Asana API or background synchronization."""
import re
from urllib.parse import urlencode, urlsplit

from .database import connect
from .security import event


def follow_up(config, audit):
    title = f"Failed audit follow-up · MLS {audit['mls_number']}"
    notes = (f"MLS: {audit['mls_number']}\nProperty: {audit['address']}\n\n"
             f"Issues recorded:\n{audit['issues'] or ''}\n\n"
             f"AuditDesk: {config.public_url.rstrip('/')}/?tab=outcome&id={audit['id']}")
    # Bound the undocumented deep link; the copy fallback retains every issue.
    draft_notes = notes if len(notes) <= 1400 else notes[:1100] + '\n\n[Full issues available in AuditDesk]\n' + notes[notes.rfind('AuditDesk:'):]
    draft = 'https://app.asana.com/0/-/create_task?' + urlencode({'name': title[:200], 'notes': draft_notes})
    return title, notes, draft


def save_task_link(config, audit_id, raw):
    value = raw.strip()
    if len(value) > 2048:
        raise ValueError('Asana task links must be 2,048 characters or fewer.')
    if value:
        try:
            parsed = urlsplit(value)
        except ValueError:
            raise ValueError('Paste an HTTPS task link from app.asana.com.') from None
        task_path = re.fullmatch(r'/0/\d+/\d+(?:/f)?/?|/1/\d+/(?:project/\d+/)?task/\d+/?', parsed.path)
        if parsed.scheme != 'https' or parsed.netloc != 'app.asana.com' or not task_path or any(ord(c) < 32 for c in value):
            raise ValueError('Paste an HTTPS task link from app.asana.com.')
    with connect(config.database_path) as db:
        audit = db.execute('SELECT outcome FROM audits WHERE id=?', (audit_id,)).fetchone()
        if not audit or audit['outcome'] != 'failed':
            raise ValueError('Asana follow-up is available only for a recorded failed audit.')
        db.execute('UPDATE audits SET asana_task_url=? WHERE id=?', (value or None, audit_id))
        event(db, 'audit.asana_link_saved' if value else 'audit.asana_link_removed', audit_id, value)
        db.commit()
