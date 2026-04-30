#!/usr/bin/env python3
"""
SplitRD — PRD to Linear Agent
Converts a PRD into a Linear project with tickets, automatically.

Usage:
    python agent.py your-prd.md
    python agent.py your-prd.md --dry-run     # preview without creating anything
"""

import os
import sys
import json
import argparse
import requests
from pathlib import Path
from dotenv import load_dotenv
import anthropic

load_dotenv()

# ── config ────────────────────────────────────────────────────────────────────
ANTHROPIC_KEY  = os.getenv("ANTHROPIC_API_KEY")
LINEAR_KEY     = os.getenv("LINEAR_API_KEY")
LINEAR_API_URL = "https://api.linear.app/graphql"
TARGET_TEAM    = os.getenv("LINEAR_TEAM", "User Conversion")
TARGET_STATE   = os.getenv("LINEAR_STATE", "Todo")
CLAUDE_MODEL   = "claude-sonnet-4-6"

PRIORITY_MAP = {"urgent": 1, "high": 2, "medium": 3, "low": 4}
TYPE_EMOJI   = {
    "frontend":  "🎨",
    "backend":   "⚙️",
    "design":    "✏️",
    "marketing": "📣",
    "qa":        "🧪",
    "pm":        "📋",
    "data":      "📊",
}


# ── linear api ────────────────────────────────────────────────────────────────
def linear(query: str, variables: dict = None) -> dict:
    resp = requests.post(
        LINEAR_API_URL,
        json={"query": query, "variables": variables or {}},
        headers={"Authorization": LINEAR_KEY, "Content-Type": "application/json"},
        timeout=15,
    )
    
    resp.raise_for_status()
    payload = resp.json()
    if "errors" in payload:
        raise RuntimeError(f"Linear API error: {payload['errors'][0]['message']}")
    return payload["data"]





def get_team_and_state() -> tuple[str, str]:
    """Return (team_id, todo_state_id) for the User Conversion team."""
    data = linear("""
        query {
          teams {
            nodes {
              id
              name
              states {
                nodes { id name type }
              }
            }
          }
        }
    """)

    teams = data["teams"]["nodes"]
    team = next(
        (t for t in teams if t["name"].lower() == TARGET_TEAM.lower()), None
    )
    if not team:
        available = [t["name"] for t in teams]
        raise RuntimeError(
            f"Team '{TARGET_TEAM}' not found in your workspace.\n"
            f"Available teams: {available}\n"
            f"Update TARGET_TEAM in agent.py if needed."
        )

    states = team["states"]["nodes"]
    state = next(
        (s for s in states if s["name"].lower() == TARGET_STATE.lower()), None
    )
    if not state:
        # fall back to first unstarted state
        state = next((s for s in states if s["type"] == "unstarted"), states[0])
        print(f"  ⚠  '{TARGET_STATE}' state not found — using '{state['name']}' instead")

    return team["id"], state["id"]

def get_project_status_id(status_type: str = "planned") -> str | None:
    """Fetch the project status ID matching the given type (backlog/planned/inProgress/completed/cancelled)."""
    data = linear("""
        query {
          projectStatuses {
            nodes { id name type }
          }
        }
    """)
    statuses = data["projectStatuses"]["nodes"]
    match = next(
        (s for s in statuses if s["type"].lower() == status_type.lower()), None
    )
    return match["id"] if match else None


def create_project(name: str, summary: str, description: str, team_id: str) -> dict:
    status_id = get_project_status_id("planned")
    input_data = {
        "name": name,
        "description": summary[:255],   # short summary only, max 255 chars
        "content": description,          # full markdown goes here
        "teamIds": [team_id],
    }
    if status_id:
        input_data["statusId"] = status_id

    data = linear(
        """
        mutation CreateProject($input: ProjectCreateInput!) {
          projectCreate(input: $input) {
            success
            project { id name url }
          }
        }
        """,
        {"input": input_data},
    )
    result = data["projectCreate"]
    if not result["success"]:
        raise RuntimeError("Linear returned success=false when creating project.")
    return result["project"]


def create_issue(
    title: str,
    description: str,
    ticket_type: str,
    priority: str,
    team_id: str,
    project_id: str,
    state_id: str,
) -> dict:
    """Create a Linear issue and return {id, identifier, title, url}."""
    full_description = (
        f"**Type:** {ticket_type.capitalize()}\n\n"
        + description
    )
    data = linear(
        """
        mutation CreateIssue($input: IssueCreateInput!) {
          issueCreate(input: $input) {
            success
            issue { id identifier title url }
          }
        }
        """,
        {
            "input": {
                "title": title,
                "description": full_description,
                "teamId": team_id,
                "projectId": project_id,
                "stateId": state_id,
                "priority": PRIORITY_MAP.get(priority.lower(), 3),
            }
        },
    )
    result = data["issueCreate"]
    if not result["success"]:
        raise RuntimeError(f"Linear returned success=false for issue: {title}")
    return result["issue"]


# ── claude calls ──────────────────────────────────────────────────────────────
def generate_project_details(prd: str, client: anthropic.Anthropic) -> dict:
    """
    Pass 1 — ask Claude to extract a structured project from the PRD.
    Returns: {name, summary, description}
    """
    resp = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=1500,
        system=(
            "You are a senior product manager. "
            "Return ONLY valid JSON — no markdown fences, no explanation."
        ),
        messages=[
            {
                "role": "user",
                "content": f"""Read this PRD and return a JSON object with project details for Linear.

Return EXACTLY this schema:
{{
  "name": "Short project name in Title Case (3–6 words)",
  "summary": "2–3 sentence executive summary: what this is, who it's for, why it matters.",
  "description": "Full markdown project description. Use these sections:\\n\\n## Overview\\nDetailed description of the initiative.\\n\\n## Goals\\nBulleted list of specific goals.\\n\\n## Success Metrics\\nHow will we know this worked? Include numbers where possible.\\n\\n## Scope\\nWhat IS included in this project.\\n\\n## Out of Scope\\nWhat is explicitly NOT included.\\n\\n## Open Questions\\nAmbiguities or decisions that still need to be made."
}}

Be thorough in the description — this will be the single source of truth for the team in Linear.

PRD:
{prd}""",
            }
        ],
    )
    raw = resp.content[0].text.replace("```json", "").replace("```", "").strip()
    return json.loads(raw)


def generate_tickets(prd: str, project_name: str, client: anthropic.Anthropic) -> list[dict]:
    """
    Pass 2 — ask Claude to break the PRD into cross-functional tickets.
    Returns list of ticket dicts.
    """
    resp = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=4000,
        system=(
            "You are a senior PM and tech lead. "
            "Return ONLY valid JSON — no markdown fences, no explanation."
        ),
        messages=[
            {
                "role": "user",
                "content": f"""Break this PRD into Linear tickets for the project "{project_name}".
Cover all disciplines needed: frontend, backend, design, marketing, qa, pm, data.

Return EXACTLY this schema:
{{
  "tickets": [
    {{
      "title": "Imperative verb + specific object (max 10 words, e.g. 'Build filter API endpoint for task queries')",
      "type": "frontend | backend | design | marketing | qa | pm | data",
      "priority": "urgent | high | medium | low",
      "description": "## What\\nA clear description of what needs to be built or done.\\n\\n## Why\\nContext: why this ticket exists and what it unlocks.\\n\\n## Acceptance Criteria\\n- [ ] Specific, testable criterion\\n- [ ] Specific, testable criterion\\n- [ ] Specific, testable criterion"
    }}
  ]
}}

Rules:
- 6 to 12 tickets total
- Each ticket = 1–5 days of work for one person
- Titles are specific and action-oriented
- Acceptance criteria are measurable, not vague
- Include at least one design ticket if there is any UI involved
- Include at least one QA ticket
- Dependencies should be implied by the order (put backend before frontend where applicable)

PRD:
{prd}""",
            }
        ],
    )
    raw = resp.content[0].text.replace("```json", "").replace("```", "").strip()
    return json.loads(raw)["tickets"]


# ── cli entry point ───────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        description="Convert a PRD into a Linear project with tickets.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python agent.py my-feature.md
  python agent.py my-feature.md --dry-run
        """,
    )
    parser.add_argument("prd_file", help="Path to your PRD (.md or .txt)")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview what would be created — does NOT call Linear",
    )
    args = parser.parse_args()

    # ── validate env ─────────────────────────────────────────────────────────
    missing = []
    if not ANTHROPIC_KEY:
        missing.append("ANTHROPIC_API_KEY")
    if not LINEAR_KEY and not args.dry_run:
        missing.append("LINEAR_API_KEY")
    if missing:
        print(f"\n❌  Missing environment variables: {', '.join(missing)}")
        print("    Copy .env.example → .env and fill in your keys.\n")
        sys.exit(1)

    # ── read PRD ──────────────────────────────────────────────────────────────
    prd_path = Path(args.prd_file)
    if not prd_path.exists():
        print(f"\n❌  File not found: {prd_path}\n")
        sys.exit(1)

    prd_content = prd_path.read_text(encoding="utf-8").strip()
    if not prd_content:
        print(f"\n❌  PRD file is empty: {prd_path}\n")
        sys.exit(1)

    print(f"\n📄  PRD loaded: {prd_path.name}  ({len(prd_content):,} chars)")
    if args.dry_run:
        print("    [DRY RUN — nothing will be created in Linear]\n")
    else:
        print()

    client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)

    # ── step 1: connect to linear ─────────────────────────────────────────────
    team_id = state_id = None
    if not args.dry_run:
        print("🔗  Connecting to Linear...")
        try:
            team_id, state_id = get_team_and_state()
        except Exception as e:
            print(f"\n❌  {e}\n")
            sys.exit(1)
        print(f"    ✓  Team  : {TARGET_TEAM}")
        print(f"    ✓  State : {TARGET_STATE}\n")

    # ── step 2: generate project details ─────────────────────────────────────
    print("🤖  [1/2]  Generating project details with Claude...")
    try:
        project_details = generate_project_details(prd_content, client)
    except Exception as e:
        print(f"\n❌  Claude error (project): {e}\n")
        sys.exit(1)

    print(f"    ✓  Name    : {project_details['name']}")
    print(f"    ✓  Summary : {project_details['summary'][:80]}...")

    # ── step 3: generate tickets ──────────────────────────────────────────────
    print(f"\n🤖  [2/2]  Breaking PRD into tickets with Claude...")
    try:
        tickets = generate_tickets(prd_content, project_details["name"], client)
    except Exception as e:
        print(f"\n❌  Claude error (tickets): {e}\n")
        sys.exit(1)

    print(f"    ✓  {len(tickets)} tickets generated\n")

    # ── dry run preview ───────────────────────────────────────────────────────
    if args.dry_run:
        print("─" * 60)
        print(f"PROJECT  →  {project_details['name']}")
        print(f"SUMMARY  →  {project_details['summary']}\n")
        print(f"TICKETS  ({len(tickets)} total):")
        for i, t in enumerate(tickets, 1):
            emoji = TYPE_EMOJI.get(t.get("type", ""), "📌")
            print(f"  {i:>2}. {emoji}  [{t['type'].upper():10}] [{t['priority'].upper():6}]  {t['title']}")
        print("\n✅  Dry run complete. Remove --dry-run to create in Linear.\n")
        return

    # ── step 4: create linear project ────────────────────────────────────────
    print("📁  Creating Linear project...")
    try:
        project = create_project(
            name=project_details["name"],
            summary=project_details["summary"],
            description=project_details["description"],
            team_id=team_id,
        )
    except Exception as e:
        print(f"\n❌  Failed to create project: {e}\n")
        sys.exit(1)

    print(f"    ✓  Created : {project['name']}")
    print(f"    🔗  {project['url']}\n")

    # ── step 5: create tickets ────────────────────────────────────────────────
    print(f"⬆️   Creating {len(tickets)} tickets in Linear...")
    created = []
    failed  = []

    for ticket in tickets:
        try:
            issue = create_issue(
                title=ticket["title"],
                description=ticket["description"],
                ticket_type=ticket.get("type", "task"),
                priority=ticket.get("priority", "medium"),
                team_id=team_id,
                project_id=project["id"],
                state_id=state_id,
            )
            emoji = TYPE_EMOJI.get(ticket.get("type", ""), "📌")
            print(f"    {emoji}  [{issue['identifier']}]  {ticket['title'][:55]}")
            created.append(issue)
        except Exception as e:
            print(f"    ⚠   Failed: {ticket['title'][:50]}  ({e})")
            failed.append(ticket)

    # ── summary ───────────────────────────────────────────────────────────────
    print()
    print("─" * 60)
    print(f"✅  Done!  {len(created)} tickets created, {len(failed)} failed.")
    print(f"🔗  Project: {project['url']}")
    if failed:
        print(f"\n⚠   Failed tickets:")
        for t in failed:
            print(f"    - {t['title']}")
    print()


if __name__ == "__main__":
    main()
