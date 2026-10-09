"""IT recovery controls; live replacement remains a stopped-service operation."""
import shlex

from ..backup import restore_details
from .common import esc, retry_token


def recovery_view(config, identifier=''):
    if config.env == 'development':
        return '''<section class="panel admin-panel"><div class="panel-head"><h2>Backup and restore</h2></div>
            <div class="admin-content"><p>Recovery is unavailable in this disposable development sandbox. Test and production administrators can download a backup and validate a restore upload here.</p></div></section>'''
    staged = ''
    if identifier:
        details = restore_details(config, identifier)
        command = ('python main.py restore --input ' + shlex.quote(details['path'])
                   + " --confirm 'RESTORE STOPPED AUDITDESK'")
        staged = f'''<section class="panel admin-panel"><div class="panel-head"><h2>Validated restore file</h2></div>
            <div class="admin-content"><dl class="service-settings"><div><dt>Source schema</dt><dd>{details['schema']}</dd></div>
            <div><dt>Backup size</dt><dd>{details['bytes'] / 1024 / 1024:.2f} MiB</dd></div>
            <div><dt>Listings / audits / runs</dt><dd>{details['listings']:,} / {details['audits']:,} / {details['runs']:,}</dd></div></dl>
            <p class="form-help">Integrity, supported schema, migration, and administrator access checks passed. The staged file retains its original schema.</p>
            <p class="form-help">SHA-256: <code>{details['sha256']}</code></p>
            <ol><li>Take a current backup, then stop AuditDesk in Coolify. Keep its persistent volume attached.</li>
            <li>Run this command in a one-off container using the chosen AuditDesk image, the same persistent volume, and runtime identity configuration:</li></ol>
            <pre class="recovery-command"><code>{esc(command)}</code></pre>
            <p>Restore saves a pre-restore backup, then replaces the database. Newer records and changes will be lost. Restart the service and verify login and record counts. Check existing email delivery records before resuming outgoing mail.</p>
            <p class="form-help">A rollback to an older image must use a backup supported by that image and its restore command. After recovery, remove the staged upload from the private restore-uploads directory.</p></div></section>'''
    return f'''<section class="panel admin-panel"><div class="panel-head"><div><h2>Download database backup</h2><p>A consistent snapshot of AuditDesk's persistent records.</p></div></div>
        <div class="admin-content"><p>Includes listings, contacts, audits, attached paperwork, email history, settings, and application access. Store the backup in approved encrypted storage. Integration credentials and the session signing key are not included.</p>
        <form method="post" action="/manage/backup"><input type="hidden" name="token" value="{retry_token(config, 'backup')}">
        <button type="submit" class="primary-button">Download backup</button></form></div></section>
        <section class="panel admin-panel"><div class="panel-head"><div><h2>Prepare a restore</h2><p>Validate a backup before stopping the service.</p></div></div>
        <div class="admin-content"><p>Uploading a file does not replace any records. Live replacement requires the stopped-service restore command after validation.</p>
        <form method="post" action="/manage/restore" enctype="multipart/form-data" class="restore-upload">
        <input type="hidden" name="token" value="{retry_token(config, 'restore')}">
        <label for="restore-backup">AuditDesk SQLite backup (maximum 512 MiB)<input id="restore-backup" type="file" name="backup" accept=".sqlite3,.sqlite,.db" required></label>
        <button type="submit">Validate and stage backup</button></form></div></section>{staged}'''
