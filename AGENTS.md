# AGENTS.md — OpenAI Codex CLI Instructions

You are operating inside the `boss-zhipin-agent` repository.
Before executing any BOSS Zhipin recruitment task (searching jobs, applying, checking chat messages, replying to HR, uploading resumes, or blocking companies), read [`SKILL.md`](./SKILL.md) and inspect `config.json` (or `config.example.json` if `config.json` does not exist yet).

## Quick Commands for Codex
- Check new recruiter messages: `python scripts/boss_cli.py check-chat`
- Apply to 5 filtered jobs: `python scripts/boss_cli.py apply --limit 5`
- Reply / send PDF resume: `python scripts/boss_cli.py reply --company "<keyword>" --text "<reply>" [--send-resume "<pdf_keyword>"]`
- Block companies: `python scripts/boss_cli.py shield --add "<company_name>"`

Always adhere strictly to the **Iron Rules for Replying to Recruiters** in [`SKILL.md`](./SKILL.md):
1. Reply directly and naturally to what the recruiter said (`根据对方回的消息去回复`).
2. Use **only** facts from the user's desensitized resume (`严格依据脱敏简历，不要回复其他信息`).
3. Keep the tone knowledgeable, concise, and humble (`说是我的 AI 助手，且回答得有水平且谦逊`).
