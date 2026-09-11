---
name: make_video
description: Generate a knowledge-sharing video with PPT slides and voice narration
triggers:
  - make a video
  - create a video
  - generate a video
  - make a ppt video
  - 做视频
  - 做个视频
  - 做一个视频
  - 知识分享视频
  - 带语音讲解
---

# Make Video Skill

When the user asks to make a video (with or without PPT/voice), follow these steps:

## Environment Check
- Check installed packages: `python -c "import edge_tts, moviepy, PIL"` (do NOT use shell chaining with && or |)
- Find ffmpeg: `python -c "import shutil; print(shutil.which('ffmpeg'))"`
- Find Chinese fonts: `ls /c/Windows/Fonts/` or `python -c "import os; print(os.path.exists('C:/Windows/Fonts/msyh.ttc'))"`

## Implementation Strategy
1. **Slides**: Use Pillow (PIL) to render 1920x1080 slide images with Chinese text (fonts: msyh.ttc, msyhbd.ttc)
2. **Voice**: Use `edge_tts.Communicate(text, voice, rate).save()` — voice "zh-CN-XiaoxiaoNeural", async, needs `import edge_tts` and `asyncio.run()`
3. **Video**: Use ffmpeg directly (found via `shutil.which('ffmpeg')`) — create clips per slide+audio, then concat

## Critical Rules
- Do NOT use `ffmpeg` as a `run_command` argument — it is not in the allowed list
- Do NOT use shell metacharacters (`&`, `|`, `>`, `;`) in `run_command` — write a `.py` file and run it with `python script.py`
- Do NOT use `dot` command (graphviz) — use matplotlib or PIL instead
- Use absolute paths on Windows (e.g., `C:/Users/<name>/Desktop/video.py`)
- Write the full script in ONE `write_file` call, then run it

## Output
- Save the final video to the user's home directory or Desktop
- Report the file path, resolution, duration, and file size
- Save the generation script alongside the video for re-editing
