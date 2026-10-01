"""Daily intake and brokerage statistics presentation."""
from ..brokerage_report import PERIODS, brokerage_statistics
from ..report import daily_audit_report
from .common import badge, esc, sort_heading


def report_view(config):
    daily = daily_audit_report(config)
    total = sum(row["listings"] for row in daily)
    audited = sum(row["audited"] for row in daily)
    overall = f"{100 * audited / total:.1f}%" if total else "—"
    rows = ""
    for row in daily:
        recorded = row["status"] in {"Recorded", "Run had errors"} or bool(row["listings"])
        listings = str(row["listings"]) if recorded else "—"
        selected = str(row["audited"]) if recorded else "—"
        percentage = f'{row["percentage"]:.1f}%' if row["percentage"] is not None else "—"
        rows += f'<tr><td><strong>{row["date"].strftime("%b %d, %Y")}</strong></td><td>{listings}</td><td>{selected}</td><td>{percentage}</td><td>{badge(row["status"].lower().replace(" ", "_"))}</td></tr>'
    return f'''<section class="report-summary"><div><span>Listings considered</span><strong>{total}</strong></div>
        <div><span>Audited</span><strong>{audited}</strong></div><div><span>Audit percentage</span><strong>{overall}</strong></div></section>
        <section class="panel report-panel"><div class="panel-head"><div><h2>Daily audit report</h2><p>Past 90 days, newest first</p></div></div>
        <div class="report-note">Counts are unique new Active listings first processed by the app on each date in {esc(config.timezone)}, including manual test runs. A day without a completed run is shown as unavailable rather than zero.</div>
        <div class="table-wrap"><table data-columns="daily-report" class="report-table"><thead><tr><th>Date</th><th>Total listings</th><th>Audited listings</th><th>Audit %</th><th>Run status</th></tr></thead><tbody>{rows}</tbody></table></div></section>'''



def brokerage_view(config, period):
    report = brokerage_statistics(config, period)
    def rate_text(value):
        return f"{value:.1f}%" if value is not None else "—"
    percentage = f'{report["percentage"]:.1f}%' if report["percentage"] is not None else "—"
    pass_rate = f'{report["pass_rate"]:.1f}%' if report["pass_rate"] is not None else "—"
    fail_rate = f'{report["fail_rate"]:.1f}%' if report["fail_rate"] is not None else "—"
    options = "".join(f'<option value="{key}" {"selected" if key == period else ""}>{label}</option>'
                      for key, (_, label) in PERIODS.items())
    def cells(row):
        return (f'<td data-sort="{row["listings"]}">{row["listings"]:,}</td>'
                f'<td data-sort="{row["audited"]}">{row["audited"]:,}</td>'
                f'<td data-sort="{row["percentage"]}">{row["percentage"]:.1f}%</td>'
                f'<td data-sort="{row["passed"]}">{row["passed"]:,}</td>'
                f'<td data-sort="{row["pass_rate"] if row["pass_rate"] is not None else ""}">{rate_text(row["pass_rate"])}</td>'
                f'<td data-sort="{row["failed"]}">{row["failed"]:,}</td>'
                f'<td data-sort="{row["fail_rate"] if row["fail_rate"] is not None else ""}">{rate_text(row["fail_rate"])}</td>')
    rows = ""
    for index, row in enumerate(report["rows"]):
        branch_label = "branch" if row["branch_count"] == 1 else "branches"
        address = row["branches"][0]["address"] if row["branch_count"] == 1 else None
        details = f'<small>{esc(address) if address else str(row["branch_count"]) + " " + branch_label}</small>'
        button = (f'<button type="button" class="branch-toggle" aria-expanded="false" '
                  f'aria-controls="brokerage-branches-{index}" data-count="{row["branch_count"]}">'
                  f'View {row["branch_count"]} {branch_label}</button>')
        parent = (f'<tr class="brokerage-parent"><td data-sort="{esc(row["name"])}">'
                  f'<strong>{esc(row["name"])}</strong>{details}{button}</td>{cells(row)}</tr>')
        branches = "".join(
            f'<tr class="branch-row" id="brokerage-branches-{index}-{branch_index}" hidden>'
            f'<td><span class="branch-indent">{esc(branch["address"] or "Address unavailable")}</span>'
            f'<small>Office {esc(branch["office_id"] or "unknown")}</small></td>{cells(branch)}</tr>'
            for branch_index, branch in enumerate(row["branches"]))
        rows += f'<tbody class="brokerage-group" id="brokerage-branches-{index}">{parent}{branches}</tbody>'
    if not rows:
        rows = '<tbody><tr><td colspan="8" class="empty">No listings were recorded in this period.</td></tr></tbody>'
    coverage = (f'Available app records begin {report["first_recorded"]:%b %d, %Y}; earlier dates in this period have no app history.'
                if report["first_recorded"] and report["first_recorded"] > report["start"] else
                "The selected period is covered by available app history.")
    headings = "".join((sort_heading("Brokerage"), sort_heading("Listings", "number", True),
                        sort_heading("Audited", "number"), sort_heading("Audit %", "number"),
                        sort_heading("Passed", "number"), sort_heading("Pass rate", "number"),
                        sort_heading("Failed", "number"), sort_heading("Fail rate", "number")))
    return f'''<div class="brokerage-controls"><form method="get" action="/" class="brokerage-period-form">
        <input type="hidden" name="tab" value="brokerages"><label for="brokerage-period">Reporting period</label>
        <select id="brokerage-period" name="period">{options}</select><button type="submit">Apply</button></form>
        <a class="primary-button report-download" href="/reports/brokerages.pdf?period={period}">Export branded PDF</a></div>
        <section class="report-summary brokerage-summary"><div><span>Listings considered</span><strong>{report["total"]:,}</strong></div>
        <div><span>Audited</span><strong>{report["audited"]:,}</strong></div>
        <div><span>Audit percentage</span><strong>{percentage}</strong></div>
        <div><span>Completed audits</span><strong>{report["completed"]:,}</strong></div>
        <div><span>Pass rate</span><strong>{pass_rate}</strong></div>
        <div><span>Fail rate</span><strong>{fail_rate}</strong></div></section>
        <section class="panel brokerage-panel"><div class="panel-head"><div><h2>Brokerage statistics</h2>
        <p>{report["start"]:%b %d, %Y} – {report["end"]:%b %d, %Y} · {report["period_label"].title()} · {len(report["rows"]):,} brokerages</p></div></div>
        <div class="report-note">Branches with the same brokerage name are combined. Select “View branches” to see each office address and its statistics. Listings are unique new Active listings first processed by this app; audited listings have a saved audit selection. Pass and fail rates use completed audits only ({report["completed"]:,} of {report["audited"]:,} selected audits have a result). Includes manual test runs. {esc(coverage)} This is not a count of every MLS listing.</div>
        <div class="table-wrap"><table data-columns="brokerages" class="brokerage-table" data-sortable data-sort-groups><thead><tr>{headings}</tr></thead>{rows}</table></div></section>'''

