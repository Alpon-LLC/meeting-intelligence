# Meeting Note Templates

These markdown templates define the section structure used by the **`meeting-transcript-to-notion`** skill when creating Notion pages from transcripts or meeting notes. In Hermes, load any file via `skill_view("meeting-transcript-to-notion", "templates/<filename>.md")`.

---

## Available templates

| File | Use for |
|---|---|
| `default-meeting-notes.md` | Internal team syncs, general meetings |
| `client-call.md` | External / client-facing calls; separates client and internal actions |
| `retro.md` | Sprint or project retrospectives |

---

## How the skill uses these

When no Notion template page URL or database template is provided, the skill reads the appropriate `.md` file from this folder and uses its headings as the content structure. It then fills each section with extracted content from the transcript.

To use a specific local template, tell the skill which one:

> "Use the client call template"
> "Use the retro template"
> "Use the default template"

---

## How to use a Notion template instead

If you have the meeting note template stored as a Notion page, pass its URL directly:

> "Use this Notion template: ${CONFIGURED_SOURCE_URL}"

The skill will fetch the page and use its heading structure instead of the local `.md` files.

---

## Notion template space

A dedicated Notion space — **"Meeting note templates (AI)"** — can hold Notion-native versions of these templates so they can be passed by URL and are accessible to the whole team. Recommended structure:

```
Meeting note templates (AI)              ← parent page
  ├── Meeting notes – Standard           ← mirrors default-meeting-notes.md
  ├── Meeting notes – Client Call        ← mirrors client-call.md
  └── Meeting notes – Retro              ← mirrors retro.md
```

To set this up: create the parent page in Notion, add one child page per template, paste the headings from the corresponding `.md` file, and save the parent page URL here for reference.

**Parent page URL:** *(add here once created)*

---

## Adding a new template

1. Create a new `.md` file in this folder using the same heading structure as the others.
2. Add it to the table above.
3. Optionally mirror it as a child page in the Notion "Meeting note templates (AI)" space.
4. Reference it in `SKILL.md` under "Step 5: Build page content".
