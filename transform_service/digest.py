"""Weekly digest formatter — generates human-readable meeting summary."""

from __future__ import annotations

from datetime import date, timedelta

import memgraph_client


async def generate_weekly_digest() -> str:
    data = memgraph_client.get_weekly_digest_data()
    today = date.today()
    start = today - timedelta(days=7)

    meetings = data.get("meetings", [])
    decisions = data.get("decisions", [])
    actions_created = data.get("actions_created", [])
    actions_closed = data.get("actions_closed", [])
    top_topics = data.get("top_topics", [])

    lines = [
        "╔══════════════════════════════════════╗",
        "║  Meeting Memory — Weekly Digest      ║",
        f"║  Week of {start} to {today}  ║",
        "╚══════════════════════════════════════╝",
        "",
        f"📅 MEETINGS THIS WEEK ({len(meetings)})",
    ]

    for row in meetings:
        m = row.get("m", {})
        title = m.get("title", "Untitled")
        mdate = m.get("date", "")
        lines.append(f"  • {title} — {mdate}")

    lines += ["", f"✅ DECISIONS MADE ({len(decisions)})"]
    for row in decisions:
        d = row.get("d", {})
        lines.append(f"  • {d.get('text', '')}")

    lines += [
        "",
        f"📌 ACTION ITEMS CREATED ({len(actions_created)}) / CLOSED ({len(actions_closed)})",
    ]
    for row in actions_created:
        a = row.get("a", {})
        priority = a.get("priority", "medium")
        task = a.get("task", "")
        due = a.get("due", "no due date")
        owner = a.get("owner", "unassigned")
        lines.append(f"  • [{priority}] {task} — due {due} (owner: {owner})")

    lines += ["", "🔥 TOP TOPICS"]
    for i, row in enumerate(top_topics, 1):
        name = row.get("name", "")
        freq = row.get("freq", 0)
        lines.append(f"  {i}. {name} — mentioned in {freq} meeting(s)")

    return "\n".join(lines)
