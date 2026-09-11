---
name: make_ppt
description: Generate a PowerPoint presentation with modern design
triggers:
  - make a ppt
  - make a presentation
  - create slides
  - 做个ppt
  - 做一个ppt
  - 做PPT
  - 帮我做个ppt
---

# Make PPT Skill

When the user asks to make a PPT/presentation, follow these steps:

## Environment Check
- Check: `python -c "import pptx"` (python-pptx must be installed)
- Find Chinese fonts: `python -c "import os; print(os.path.exists('C:/Windows/Fonts/msyhbd.ttc'))"`

## Implementation Strategy
1. Write a Python script using `python-pptx` to create the presentation
2. Use 16:9 widescreen format: `prs.slide_width = Inches(13.333); prs.slide_height = Inches(7.5)`
3. Use blank layout (layout index 6) for full custom design
4. Add shapes, textboxes, colored rectangles for modern look
5. Use Chinese font: "Microsoft YaHei" or font file path

## Critical Rules
- Write the full script in ONE `write_file` call, then run with `python script.py`
- Do NOT use shell metacharacters in `run_command`
- Use `prs.save(path)` to save the .pptx file
- If user doesn't specify a topic, ask them first (but still commit to producing something)

## Output
- Save the .pptx file to the user's home or Desktop
- Report the file path, slide count, and file size
