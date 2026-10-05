"""Compact, labelled row controls using the application's existing SVG style."""
from .common import esc

ICONS = {
    'retry': '<path d="M3 11a9 9 0 1 1 2.6 7M3 4v7h7"/>',
    'result': '<path d="M9 5H5v16h14v-8M9 3h6v4H9zM13 14l7-7 2 2-7 7-3 1z"/>',
    'asana': '<circle cx="12" cy="6" r="3"/><circle cx="6" cy="16" r="3"/><circle cx="18" cy="16" r="3"/>',
    'details': '<path d="M10 13a5 5 0 0 0 7 0l3-3a5 5 0 0 0-7-7l-2 2M14 11a5 5 0 0 0-7 0l-3 3a5 5 0 0 0 7 7l2-2"/>',
    'notice': '<path d="M3 5h18v14H3zM3 5l9 7 9-7"/>',
    'response': '<path d="M4 4h16v13H9l-5 4zM8 10l3 3 5-6"/>',
}


def action_icon(kind):
    return f'<svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">{ICONS[kind]}</svg>'


def action_button(kind, label):
    return f'<button type="submit" class="row-action" aria-label="{esc(label)}" title="{esc(label)}">{action_icon(kind)}</button>'


def action_link(kind, label, href, *, external=False):
    target = ' target="_blank" rel="noopener noreferrer"' if external else ''
    return f'<a class="row-action" href="{esc(href)}" aria-label="{esc(label)}" title="{esc(label)}"{target}>{action_icon(kind)}</a>'
