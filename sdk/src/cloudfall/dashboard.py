"""Render a dependency-free static Cloudfall operations dashboard."""

# ruff: noqa: E501

from __future__ import annotations

import json
from dataclasses import dataclass
from html import escape
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

    from cloudfall.operations import (
        DomainCheck,
        DomainOperations,
        FleetOperations,
        MonitoredFilesystem,
        OperationsTask,
        ServerOperations,
        SmartDeviceEvidence,
    )

_FILESYSTEM_WARNING_BASIS_POINTS = 8_500
_FILESYSTEM_CRITICAL_BASIS_POINTS = 9_500
_SMART_WARNING_PERCENT_USED = 90


@dataclass(frozen=True, slots=True)
class DashboardArtifacts:
    """Files emitted by one dashboard build."""

    index: Path
    operations: Path

    def as_dict(self) -> dict[str, str]:
        """Serialize generated artifact paths."""
        return {
            "index": str(self.index),
            "operations": str(self.operations),
        }


def build_dashboard(
    operations: FleetOperations, output_directory: Path
) -> DashboardArtifacts:
    """Write static HTML and its machine-readable operations payload."""
    output_directory.mkdir(parents=True, exist_ok=True)
    index_path = output_directory / "index.html"
    operations_path = output_directory / "operations.json"
    _atomic_write(
        operations_path,
        f"{json.dumps(operations.as_dict(), indent=2, sort_keys=True)}\n",
    )
    _atomic_write(index_path, _render_html(operations))
    return DashboardArtifacts(index=index_path, operations=operations_path)


def _atomic_write(path: Path, content: str) -> None:
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)


def _render_html(operations: FleetOperations) -> str:
    payload = operations.as_dict()
    summary = payload["summary"]
    if not isinstance(summary, dict):
        message = "serialized operations summary is not an object"
        raise TypeError(message)
    tasks = summary["tasks"]
    if not isinstance(tasks, dict):
        message = "serialized operations task summary is not an object"
        raise TypeError(message)
    server_cards = "".join(_render_server(server) for server in operations.servers)
    domain_cards = "".join(_render_domain(domain) for domain in operations.domains)
    if not domain_cards:
        domain_cards = '<p class="empty">No desired public domains.</p>'
    task_rows = "".join(_render_task_row(task) for task in operations.tasks)
    if not task_rows:
        task_rows = (
            '<tr><td colspan="5" class="empty">No open tasks from current '
            "evidence.</td></tr>"
        )
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Cloudfall Operations</title>
  <style>
    :root {{ color-scheme: dark; --bg: #0c1117; --panel: #151c24;
      --line: #293442; --text: #e7edf5; --muted: #91a0b2;
      --healthy: #48c78e; --warning: #ffbd59; --critical: #ff6577;
      --unknown: #91a0b2; }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; background: var(--bg); color: var(--text);
      font: 15px/1.5 ui-sans-serif, system-ui, -apple-system, sans-serif; }}
    main {{ width: min(1180px, calc(100% - 32px)); margin: 40px auto 64px; }}
    header {{ display: flex; justify-content: space-between; gap: 24px;
      align-items: end; margin-bottom: 28px; }}
    h1, h2, h3, p {{ margin-top: 0; }} h1 {{ margin-bottom: 4px; }}
    h2 {{ margin: 36px 0 14px; font-size: 19px; }}
    .muted, code {{ color: var(--muted); }}
    .badge {{ display: inline-flex; align-items: center; border: 1px solid;
      border-radius: 999px; padding: 4px 9px; font-size: 12px;
      font-weight: 700; letter-spacing: .04em; text-transform: uppercase; }}
    .healthy {{ color: var(--healthy); }} .warning {{ color: var(--warning); }}
    .critical {{ color: var(--critical); }} .unknown {{ color: var(--unknown); }}
    .summary {{ display: grid; grid-template-columns: repeat(5, 1fr); gap: 12px; }}
    .stat, .server, .domain, .table-wrap {{ background: var(--panel); border: 1px solid var(--line);
      border-radius: 12px; }}
    .stat {{ padding: 16px; }} .stat strong {{ display: block; font-size: 28px; }}
    .stat span {{ color: var(--muted); font-size: 13px; }}
    .servers {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(360px, 1fr));
      gap: 14px; }}
    .domains {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(430px, 1fr));
      gap: 14px; }}
    .server, .domain {{ padding: 18px; }} .server-head, .domain-head {{ display: flex;
      justify-content: space-between; gap: 16px; }}
    .server h3, .domain h3 {{ margin-bottom: 2px; }}
    table {{ width: 100%; border-collapse: collapse; }}
    th, td {{ text-align: left; padding: 11px 13px; border-bottom: 1px solid var(--line);
      vertical-align: top; }}
    tr:last-child td {{ border-bottom: 0; }} th {{ color: var(--muted);
      font-size: 12px; letter-spacing: .04em; text-transform: uppercase; }}
    .table-wrap {{ overflow-x: auto; }} .compact {{ margin-top: 14px; font-size: 13px; }}
    .compact th, .compact td {{ padding: 7px 8px; }}
    .usage {{ min-width: 110px; }} .bar {{ height: 6px; background: #27313d;
      border-radius: 99px; overflow: hidden; margin-top: 5px; }}
    .bar span {{ display: block; height: 100%; background: currentColor; }}
    .task-title {{ font-weight: 650; }} .task-detail {{ color: var(--muted);
      font-size: 13px; margin-top: 2px; }} .empty {{ color: var(--muted);
      padding: 28px; text-align: center; }}
    a {{ color: #7dc4ff; }} footer {{ margin-top: 20px; color: var(--muted);
      font-size: 13px; }}
    @media (max-width: 720px) {{ header {{ display: block; }}
      .summary {{ grid-template-columns: repeat(2, 1fr); }}
      .servers, .domains {{ grid-template-columns: 1fr; }} main {{ margin-top: 24px; }} }}
  </style>
</head>
<body>
<main>
  <header>
    <div><h1>Cloudfall Operations</h1><p class="muted">Evidence-backed fleet status</p></div>
    <div><span class="badge {operations.health.value}">{operations.health.value}</span>
      <div class="muted">Generated {escape(operations.generated_at.as_string())}</div></div>
  </header>
  <section class="summary" aria-label="Fleet summary">
    <div class="stat"><strong>{len(operations.domains)}</strong><span>services</span></div>
    <div class="stat"><strong>{len(operations.servers)}</strong><span>servers</span></div>
    <div class="stat critical"><strong>{tasks["critical"]}</strong><span>critical tasks</span></div>
    <div class="stat warning"><strong>{tasks["warning"]}</strong><span>warning tasks</span></div>
    <div class="stat unknown"><strong>{tasks["unknown"]}</strong><span>unknown tasks</span></div>
  </section>
  <h2>Services</h2>
  <section class="domains">{domain_cards}</section>
  <h2>Servers</h2>
  <section class="servers">{server_cards}</section>
  <h2>Open tasks</h2>
  <div class="table-wrap"><table>
    <thead><tr><th>Severity</th><th>Server</th><th>Task</th><th>Kind</th><th>Evidence</th></tr></thead>
    <tbody>{task_rows}</tbody>
  </table></div>
  <footer>Read-only projection. <a href="operations.json">View operations JSON</a>.</footer>
</main>
</body>
</html>
"""


def _render_domain(domain: DomainOperations) -> str:
    observation = (
        domain.observed_at.as_string()
        if domain.observed_at is not None
        else "No route observation"
    )
    deployed_at = (
        domain.deployed_at.as_string()
        if domain.deployed_at is not None
        else "No deployment receipt"
    )
    return f"""<article class="domain">
  <div class="domain-head"><div><h3>{escape(domain.primary_name)}</h3>
    <div class="muted">{escape(domain.domain_id.value)} ·
      {escape(domain.proxy_server_id.value)} → {escape(domain.origin_server_id.value)}</div></div>
    <span class="badge {domain.health.value}">{domain.health.value}</span></div>
  <table class="compact"><thead><tr><th>Lifecycle</th><th>Status</th><th>Evidence</th></tr></thead>
    <tbody>
      {_render_lifecycle_row("Planned", domain.planned.value, "Desired Domain exists")}
      {_render_lifecycle_row("Ready", domain.ready_to_deploy.value, "Referenced servers are active")}
      {_render_lifecycle_row("Deployed", domain.deployed.value, deployed_at)}
      {_render_lifecycle_row("Configured", domain.configuration.value, domain.configuration_detail)}
    </tbody></table>
  <table class="compact"><thead><tr><th>Route check</th><th>Status</th><th>Evidence</th></tr></thead>
    <tbody>
      {_render_domain_check("DNS / edge", domain.dns)}
      {_render_domain_check("TLS", domain.tls)}
      {_render_domain_check("Origin", domain.origin)}
      {_render_domain_check("Public", domain.public_route)}
    </tbody></table>
  <p class="muted" style="margin: 12px 0 0">{escape(observation)}</p>
</article>"""


def _render_lifecycle_row(label: str, status: str, detail: str) -> str:
    css_class = (
        "healthy"
        if status in {"yes", "compliant"}
        else "warning"
        if status in {"no", "drifted"}
        else "unknown"
    )
    return f"""<tr><td>{escape(label)}</td>
  <td><span class="badge {css_class}">{escape(status)}</span></td>
  <td class="muted">{escape(detail)}</td></tr>"""


def _render_domain_check(label: str, check: DomainCheck) -> str:
    css_class = (
        "healthy"
        if check.status.value == "healthy"
        else "warning"
        if check.status.value == "unhealthy"
        else "unknown"
    )
    return f"""<tr><td>{escape(label)}</td>
  <td><span class="badge {css_class}">{escape(check.status.value)}</span></td>
  <td class="muted">{escape(check.detail)}</td></tr>"""


def _render_server(server: ServerOperations) -> str:
    observation = (
        server.observed_at.as_string()
        if server.observed_at is not None
        else "No observation"
    )
    filesystems = "".join(
        _render_filesystem(filesystem) for filesystem in server.filesystems
    )
    if not filesystems:
        filesystems = '<tr><td colspan="3" class="muted">No filesystem data</td></tr>'
    smart_rows = "".join(
        _render_smart_device(device) for device in server.smart_devices
    )
    smart_section = (
        f"""<table class="compact"><thead><tr><th>NVMe</th><th>SMART</th>
    <th>Endurance</th><th>Temperature</th></tr></thead>
    <tbody>{smart_rows}</tbody></table>"""
        if smart_rows
        else ""
    )
    return f"""<article class="server">
  <div class="server-head"><div><h3>{escape(server.hostname)}</h3>
    <div class="muted">{escape(server.server_id.value)} · {escape(observation)}</div></div>
    <span class="badge {server.health.value}">{server.health.value}</span></div>
  <table class="compact"><thead><tr><th>Mount</th><th>Type</th><th>Usage</th></tr></thead>
    <tbody>{filesystems}</tbody></table>
  {smart_section}
  <p class="muted" style="margin: 12px 0 0">{len(server.tasks)} open tasks</p>
</article>"""


def _render_filesystem(filesystem: MonitoredFilesystem) -> str:
    severity = (
        "critical"
        if filesystem.utilization.basis_points >= _FILESYSTEM_CRITICAL_BASIS_POINTS
        else "warning"
        if filesystem.utilization.basis_points >= _FILESYSTEM_WARNING_BASIS_POINTS
        else "healthy"
    )
    percent = filesystem.utilization.percent
    width = min(percent, 100.0)
    return f"""<tr><td><code>{escape(filesystem.target.value)}</code></td>
  <td>{escape(filesystem.filesystem.value)}</td>
  <td class="usage {severity}">{percent:.1f}%<div class="bar">
    <span style="width:{width:.1f}%"></span></div></td></tr>"""


def _render_smart_device(device: SmartDeviceEvidence) -> str:
    severity = (
        "critical"
        if device.is_degraded
        else "warning"
        if device.percentage_used >= _SMART_WARNING_PERCENT_USED
        else "healthy"
    )
    return f"""<tr><td><code>{escape(device.path.value)}</code>
    <div class="muted">{escape(device.model)}</div></td>
  <td class="{severity}">{escape(device.overall_health.value)}</td>
  <td class="{severity}">{device.percentage_used}% used ·
    {device.available_spare_percent}% spare</td>
  <td>{device.temperature_celsius}°C</td></tr>"""


def _render_task_row(task: OperationsTask) -> str:
    return f"""<tr><td><span class="badge {task.severity.value}">{task.severity.value}</span></td>
  <td>{escape(task.server_id.value)}</td>
  <td><div class="task-title">{escape(task.title)}</div>
    <div class="task-detail">{escape(task.detail)}</div></td>
  <td>{escape(task.kind.value)}</td><td><code>{escape(task.source)}</code></td></tr>"""
