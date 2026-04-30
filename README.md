# SplitRD
A CLI agent that converts a PRD into a fully populated Linear project automatically.

**What it does:**
1. Reads your PRD (`.md` or `.txt`)
2. Uses Claude to generate a structured project (name, summary, goals, success metrics, scope)
3. Creates the project in Linear under your team
4. Breaks the PRD into cross-functional tickets (frontend, backend, design, marketing, QA...)
5. Creates every ticket inside the project in Linear, with descriptions and acceptance criteria

---

## Setup

**1. Clone and install dependencies**
```bash
git clone https://github.com/yourusername/specsplit-linear
cd specsplit-linear
pip install -r requirements.txt
```

**2. Add your API keys**
```bash
cp .env.example .env
```
Open `.env` and fill in:
```
ANTHROPIC_API_KEY=sk-ant-...   # from console.anthropic.com
LINEAR_API_KEY=lin_api_...     # from Linear → Settings → API → Personal tokens
```

**3. Run it**
```bash
python agent.py your-prd.md
```

---

## Usage

```bash
# Convert a PRD to Linear project + tickets
python3 agent.py my-feature.md

# Preview what would be created (no Linear API calls)
python3 agent.py my-feature.md --dry-run
```

**Example output:**
```
📄  PRD loaded: onboarding-redesign.md  (1,024 chars)

🔗  Connecting to Linear...
    ✓  Team  : User Conversion
    ✓  State : Todo

🤖  [1/2]  Generating project details with Claude...
    ✓  Name    : Onboarding Flow Redesign
    ✓  Summary : A redesigned step-by-step onboarding...

🤖  [2/2]  Breaking PRD into tickets with Claude...
    ✓  9 tickets generated

📁  Creating Linear project...
    ✓  Created : Onboarding Flow Redesign
    🔗  https://linear.app/io-net/project/...

⬆️   Creating 9 tickets in Linear...
    ✏️   [UC-42]  Design step-by-step onboarding wireframes
    ✏️   [UC-43]  Create progress bar component
    ⚙️   [UC-44]  Build onboarding state persistence API
    🎨  [UC-45]  Implement 4-step onboarding UI
    ⚙️   [UC-46]  Add skip and resume logic to onboarding flow
    📣  [UC-47]  Write copy for each onboarding step
    ⚙️   [UC-48]  Set up stuck-user email trigger (24h inactivity)
    📊  [UC-49]  Add analytics events for onboarding funnel
    🧪  [UC-50]  QA onboarding flow across browsers and edge cases

──────────────────────────────────────────────────────────────
✅  Done!  9 tickets created, 0 failed.
🔗  Project: https://linear.app/io-net/project/...
```

---

## How it works

The agent makes two Claude calls:

**Step 1 — Project analysis**
Claude reads the PRD and extracts: project name, executive summary, and a full structured description (overview, goals, success metrics, scope, open questions). This becomes the Linear project.

**Step 2 — Ticket generation**
Claude breaks the PRD into specific, sized tickets across all disciplines. Each ticket gets a type (frontend/backend/design/etc.), priority, markdown description, and acceptance criteria. These are created as Linear issues inside the project.

---

## Configuration

Set these in your `.env` file:

```bash
LINEAR_TEAM=Engineering        # your Linear team name
LINEAR_STATE=Todo              # default ticket state (Todo, Backlog, etc.)
```

To use a higher quality model, change `CLAUDE_MODEL` at the top of `agent.py`:
```python
CLAUDE_MODEL = "claude-opus-4-6"   
```

---

## Security

- API keys live in `.env`
- The agent only creates objects in Linear — it never reads, modifies, or deletes existing data
- No data is stored anywhere outside your own Linear workspace

---

## Requirements

- Python 3.10+
- Anthropic API key
- Linear API key (Personal token with read/write access)
