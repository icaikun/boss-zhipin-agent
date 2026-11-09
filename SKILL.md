---
name: boss-zhipin-agent
description: Automated BOSS Zhipin (BOSS直聘) job hunting, smart filtering, resume delivery, and natural AI assistant chat patrol via raw Chrome DevTools Protocol (CDP). Use when the user asks to search jobs, batch apply, block companies, upload PDF resumes, check recruiter messages, or reply on BOSS Zhipin.
---

# BOSS Zhipin Job Hunting & Chat Assistant Skill (`boss-zhipin-agent`)

This skill equips **OpenAI Codex CLI**, **Claude Code**, and **Google Antigravity** with a zero-Playwright, anti-detection **Raw CDP WebSocket CLI** (`scripts/boss_cli.py`) to automate job searching, multi-rule filtering, batch applying, attachment resume delivery, and recruiter message patrolling on BOSS Zhipin (`https://www.zhipin.com`).

---

## 1. Setup & Configuration

1. Copy `config.example.json` to `config.json` in the skill root directory and fill in:
   - `candidate.display_name` (e.g., `"李先生"`, desensitized name)
   - `candidate.resume_facts` (strictly factual bullet points from the user's desensitized resume)
   - `target` (city code, salary range, target job keywords, company scale codes)
   - `filters` (blocked company keywords, blocked industries, blocked office locations/addresses, outsourcing/OD filters)
2. First-time login (persists cookies in `~/.boss-edge-profile`):
   ```bash
   python scripts/boss_cli.py login
   ```

---

## 2. Core CLI Commands (`scripts/boss_cli.py`)

Always run `scripts/boss_cli.py` and parse its JSON stdout:

### Search Matching Jobs (with Private Font De-obfuscation)
Automatically decodes BOSS Zhipin's private Unicode salary glyphs (`\ue031`–`\ue03a` -> `0`–`9`) and filters out already-contacted or blacklisted companies:
```bash
python scripts/boss_cli.py search --limit 10
```

### Batch Apply (Twice-Daily Recommended, ~5 Companies/Batch)
Visits each candidate job detail page, verifies the full JD text and office address against `blocked_industry_keywords` and `blocked_address_keywords`, clicks `立即沟通`, sends a tailored AI assistant greeting, and records the company in `applied_history.json`:
```bash
python scripts/boss_cli.py apply --limit 5
```

### Check Recruiter Messages (~6s Deterministic Probe)
Scans recent conversations, ignores blocked/outsourcing companies and our own outgoing messages, and returns JSON with `new_replies`:
```bash
python scripts/boss_cli.py check-chat
```

### Reply to a Recruiter & Send Uploaded PDF Attachment Resume
Uses hardware-level mouse events (`Input.dispatchMouseEvent`) to open fixed-position modals (`.choose-resume-dialog`) and send the matching PDF resume:
```bash
# Send text reply only
python scripts/boss_cli.py reply --company "目标公司关键词" --text "好的，辛苦您了，有任何消息随时联系，谢谢！"

# Send uploaded PDF attachment resume + polite text reply
python scripts/boss_cli.py reply --company "目标公司关键词" --send-resume "后端与云原生架构_个人简历" --text "好的，我是李先生的 AI 助手，已经把他的脱敏版简历发给您了，请您查收。"
```

### Upload Local PDF Resume to BOSS Zhipin
Mounts a local PDF directly onto `<input type="file">` via `DOM.setFileInputFiles` and confirms `.upload-preview-dialog`:
```bash
python scripts/boss_cli.py upload-resume --file "./resumes/resume-desensitized.pdf"
```

### Block Companies on Privacy Settings Page
Searches and blocks all matching company entities on `https://www.zhipin.com/web/geek/privacy-set`:
```bash
python scripts/boss_cli.py shield --add "某外包公司" "某屏蔽企业"
```

---

## 3. Iron Rules for Replying to Recruiters

When `check-chat` reports `new_reply_count > 0`, you **MUST** follow these four rules before calling `boss_cli.py reply`:

1. **Natural & Contextual (`根据对方回的消息去回复`)**:
   - Read `conversation_tail` carefully to see what the recruiter actually asked or said.
   - Reply directly, concisely, and naturally like a thoughtful human assistant — **never** repeat long self-introduction templates that were already sent in the greeting.
2. **Strictly Grounded in the Desensitized Resume (`严格依据脱敏简历，绝不编造`)**:
   - Only reference facts, architectures, metrics, and tech stacks explicitly listed in `config.json -> candidate.resume_facts` (or the user's desensitized resume).
   - **Never** invent off-resume experiences, personal details, or filler excuses.
3. **Polite & Humble AI Assistant Persona (`有水平且谦逊`)**:
   - Naturally identify as the candidate's AI assistant (`我是X先生的 AI 助手`) when initiating or delivering resumes, maintaining a humble, respectful tone.
4. **Escalation to Human User**:
   - If the recruiter asks for a **WeChat ID, phone number, or specific interview time**, send a brief polite acknowledgment first and **immediately notify the user** to confirm.
