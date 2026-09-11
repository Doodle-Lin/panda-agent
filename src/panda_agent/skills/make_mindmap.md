---
name: make_mindmap
description: Generate a knowledge mind map as a high-resolution image
triggers:
  - make a mind map
  - create a mindmap
  - 思维导图
  - 做个思维导图
  - 做一张思维导图
---

# Make Mind Map Skill

When the user asks to make a mind map, follow these steps:

## Environment Check
- Check: `python -c "import matplotlib"` (do NOT chain commands with && or |)
- Do NOT use `dot` (graphviz) — it is not in the allowed commands list
- Use matplotlib or PIL for rendering instead

## Implementation Strategy
1. Use matplotlib with Chinese font support: `matplotlib.font_manager.FontProperties(fname='C:/Windows/Fonts/msyh.ttc')`
2. Draw a radial tree structure with colored branches
3. Each branch has a title, sub-nodes, and connecting lines
4. Save as high-resolution PNG (dpi=300)

## Critical Rules
- Do NOT use `dot` command — use matplotlib or PIL
- Do NOT use shell metacharacters in `run_command`
- Write a `.py` file with the full drawing logic, then run it
- Use `plt.savefig(path, dpi=300, bbox_inches='tight')` for high quality

## Output
- Save the PNG image to the user's home or Desktop
- Also save the `.py` script for re-editing
- Report file path, resolution, and file size
