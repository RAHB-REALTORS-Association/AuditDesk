"""Bounded list queries and shared filter/pagination presentation."""
from dataclasses import dataclass
from math import ceil
from urllib.parse import urlencode

from flask import abort, has_request_context, request

from .views.common import esc


@dataclass
class ListPage:
    tab: str
    size: int = 25
    number: int = 1
    query: str = ''
    statuses: tuple = ()
    total: int = 0
    choices: tuple = ()
    facet_label: str = 'Status'

    @classmethod
    def read(cls, tab):
        page = cls(tab)
        if has_request_context():
            raw_size = request.args.get('per_page', '25')
            raw_page = request.args.get('page', '1')
            if raw_size not in {'10', '25', '50', '100'} or not raw_page.isdigit() or len(raw_page) > 8 or int(raw_page) < 1:
                abort(400, 'Choose a valid page and rows per page.')
            page.size, page.number = int(raw_size), int(raw_page)
            page.query = request.args.get('q', '').strip()[:200]
            page.statuses = tuple(request.args.getlist('status')[:30])
        return page

    def clamp(self, total):
        self.total = total
        self.number = min(self.number, self.pages)

    @property
    def pages(self):
        return max(1, ceil(self.total / self.size))

    @property
    def offset(self):
        return (self.number - 1) * self.size

    def params(self):
        values = [('tab', self.tab), ('per_page', str(self.size))]
        if has_request_context():
            values += [(key, value) for key in ('period', 'sort', 'direction')
                       if (value := request.args.get(key))]
        if self.query:
            values.append(('q', self.query))
        values += [('status', value) for value in self.statuses]
        return values

    def url(self, **changes):
        return '/?' + urlencode([(key, value) for key, value in self.params() if key not in changes] + list(changes.items()))

    def filters(self):
        preserved = ''.join(f'<input type="hidden" name="{key}" value="{esc(value)}">'
                            for key, value in self.params() if key not in {'q', 'status'})
        choices = ''.join(f'<label><input type="checkbox" name="status" value="{esc(value)}" '
                          f'{"checked" if value in self.statuses else ""}> {esc(value.replace("_", " ").title())}</label>'
                          for value in self.choices)
        reset = '/?' + urlencode([('tab', self.tab)] + [(key, value) for key, value in self.params() if key == 'period'])
        return f'''<form class="list-filters" method="get" action="/">{preserved}
            <h3>Filters</h3><label>Search records<input type="search" name="q" maxlength="200" value="{esc(self.query) if self.query else ''}"></label>
            {f'<fieldset><legend>{esc(self.facet_label)}</legend><div class="filter-choices">{choices}</div></fieldset>' if choices else ''}
            <div class="filter-actions"><button type="submit" class="primary-button">Apply filters</button><a href="{esc(reset)}">Clear filters</a></div></form>'''

    def footer(self):
        hidden = ''.join(f'<input type="hidden" name="{key}" value="{esc(value)}">' for key, value in self.params() if key != 'per_page')
        options = ''.join(f'<option value="{size}" {"selected" if size == self.size else ""}>{size}</option>' for size in (10, 25, 50, 100))
        links = ''.join(f'<a href="{esc(self.url(page=number))}" aria-label="{label} page">{label}</a>' if enabled
                        else f'<span aria-disabled="true">{label}</span>' for label, number, enabled in
                        (('First', 1, self.number > 1), ('Previous', self.number - 1, self.number > 1),
                         ('Next', self.number + 1, self.number < self.pages), ('Last', self.pages, self.number < self.pages)))
        start = self.offset + 1 if self.total else 0
        end = min(self.offset + self.size, self.total)
        return f'''<div class="list-pagination"><span>{start}–{end} of {self.total:,} records</span>
            <form method="get" action="/">{hidden}<label>Rows per page <select name="per_page">{options}</select></label><button type="submit">Apply</button></form>
            <nav aria-label="List pages">{links}<span>Page {self.number} of {self.pages}</span></nav></div>'''


def query_page(db, tab, source, search, status, sorts, default):
    """SQL fragments are fixed by callers; all user values are bound parameters."""
    page = ListPage.read(tab)
    page.choices = tuple(row[0] for row in db.execute(f'SELECT DISTINCT {status} FROM ({source}) WHERE {status} IS NOT NULL ORDER BY 1'))
    where, values = [], []
    if page.query:
        where.append('(' + ' OR '.join(f'instr(lower(COALESCE({field},\'\')),lower(?))>0' for field in search) + ')')
        values.extend([page.query] * len(search))
    if page.statuses:
        where.append(f'{status} IN ({",".join("?" for _ in page.statuses)})')
        values.extend(page.statuses)
    filtered = f'SELECT * FROM ({source})' + (' WHERE ' + ' AND '.join(where) if where else '')
    page.clamp(db.execute(f'SELECT count(*) FROM ({filtered})', values).fetchone()[0])
    label = request.args.get('sort', '') if has_request_context() else ''
    field = sorts.get(label, default)
    direction = 'ASC' if has_request_context() and request.args.get('direction') == 'asc' else 'DESC'
    rows = db.execute(f'{filtered} ORDER BY {field} {direction},id DESC LIMIT ? OFFSET ?', values + [page.size, page.offset]).fetchall()
    return rows, page


def paginate_records(tab, rows, text, status=None, sorts=None, default=None):
    """Reports already aggregate records; page parent groups without splitting branches."""
    page = ListPage.read(tab)
    if status:
        page.choices = tuple(sorted({status(row) for row in rows}))
    rows = [row for row in rows if (not page.query or page.query.casefold() in text(row).casefold())
            and (not status or not page.statuses or status(row) in page.statuses)]
    label = request.args.get('sort', '') if has_request_context() else ''
    if sorts and (key := sorts.get(label, default)):
        descending = not has_request_context() or request.args.get('direction') != 'asc'
        rows.sort(key=lambda row: (row[key] is not None, row[key] or 0), reverse=descending)
    page.clamp(len(rows))
    return rows[page.offset:page.offset + page.size], page
