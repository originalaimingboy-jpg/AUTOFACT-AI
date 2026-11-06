import os
import re
import random
import subprocess
import asyncio

import edge_tts
from PIL import Image, ImageDraw, ImageFont

from commons import collect_media_assets

WIDTH = 1080
HEIGHT = 1920
FPS = 25
MIN_SECONDS = 30
MAX_SECONDS = 40
DEFAULT_SECONDS = 35
VOICE_NAME = "hi-IN-MadhurNeural"

FONT_PATHS = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf",
]


def run_command(command):
    result = subprocess.run(
        command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    )
    if result.returncode != 0:
        raise RuntimeError("FFmpeg error:\n" + result.stderr[-4000:])


def get_font(size):
    for path in FONT_PATHS:
        if os.path.exists(path):
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def wrap_text(text, max_chars=23):
    words = str(text).split()
    lines, current = [], ""
    for word in words:
        test = (current + " " + word).strip()
        if len(test) > max_chars and current:
            lines.append(current)
            current = word
        else:
            current = test
    if current:
        lines.append(current)
    return lines[:3]


def make_caption(text, output_path):
    image = Image.new("RGBA", (WIDTH, 350), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    font = get_font(66)
    lines = wrap_text(text)
    line_height = 82
    y = (350 - len(lines) * line_height) // 2
    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=font, stroke_width=3)
        x = (WIDTH - (bbox[2] - bbox[0])) // 2
        draw.text(
            (x, y), line, font=font, fill="white",
            stroke_width=4, stroke_fill="black"
        )
        y += line_height
    image.save(output_path)


async def create_voice(text, output_path):
    communicate = edge_tts.Communicate(
        text=text, voice=VOICE_NAME, rate="+8%", volume="+0%"
    )
    await communicate.save(output_path)


def get_audio_duration(path):
    result = subprocess.run(
        [
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", path
        ],
        capture_output=True, text=True
    )
    if result.returncode != 0 or not result.stdout.strip():
        raise RuntimeError("Could not read audio duration: " + result.stderr[-1500:])
    return float(result.stdout.strip())


def fit_audio(input_path, output_path, target_seconds):
    duration = get_audio_duration(input_path)
    if duration <= 0:
        raise RuntimeError("Audio duration invalid")
    speed = max(0.88, min(1.15, duration / target_seconds))
    run_command([
        "ffmpeg", "-y", "-i", input_path,
        "-filter:a", f"atempo={speed:.4f}",
        "-c:a", "libmp3lame", "-q:a", "3", output_path
    ])
    return get_audio_duration(output_path)


def create_image_scene(image_path, caption_path, output_path, duration):
    frames = max(1, int(duration * FPS))
    zoom_direction = random.choice(["in", "out"])
    if zoom_direction == "in":
        zoom = "z='min(zoom+0.0007,1.12)'"
    else:
        zoom = "z='if(eq(on,1),1.12,max(zoom-0.0007,1.0))'"

    base_filter = (
        "scale=1200:2134:force_original_aspect_ratio=increase,"
        "crop=1200:2134,"
        f"zoompan={zoom}:x='iw/2-(iw/zoom/2)':"
        f"y='ih/2-(ih/zoom/2)':d={frames}:s={WIDTH}x{HEIGHT}:fps={FPS},"
        "setsar=1,eq=contrast=1.08:saturation=1.12,format=yuv420p"
    )
    run_command([
        "ffmpeg", "-y", "-loop", "1", "-i", image_path,
        "-loop", "1", "-i", caption_path,
        "-filter_complex",
        f"[0:v]{base_filter}[base];[base][1:v]overlay=0:1200:shortest=1[out]",
        "-map", "[out]", "-t", f"{duration:.3f}", "-r", str(FPS),
        "-an", "-c:v", "libx264", "-preset", "veryfast",
        "-pix_fmt", "yuv420p", output_path
    ])


def create_video_scene(video_path, caption_path, output_path, duration):
    run_command([
        "ffmpeg", "-y", "-stream_loop", "-1", "-i", video_path,
        "-loop", "1", "-i", caption_path,
        "-filter_complex",
        (
            f"[0:v]scale=1200:2134:force_original_aspect_ratio=increase,"
            f"crop=1200:2134,fps={FPS},"
            "eq=contrast=1.08:saturation=1.12,setsar=1[base];"
            "[base][1:v]overlay=0:1200:shortest=1[out]"
        ),
        "-map", "[out]", "-t", f"{duration:.3f}", "-r", str(FPS),
        "-an", "-c:v", "libx264", "-preset", "veryfast",
        "-pix_fmt", "yuv420p", output_path
    ])


def _scene_caption(scenes, index):
    scene = scenes[min(index, len(scenes) - 1)]
    return str(scene.get("caption") or scene.get("title") or "").strip()


def build_video(plan, output_dir, voice_name=VOICE_NAME, target_seconds=DEFAULT_SECONDS):
    global VOICE_NAME
    VOICE_NAME = voice_name
    os.makedirs(output_dir, exist_ok=True)

    target_seconds = max(MIN_SECONDS, min(MAX_SECONDS, int(target_seconds)))
    scenes = plan.get("scenes", [])
    if not scenes:
        raise ValueError("Plan has no scenes")
    scenes = scenes[:7]
    if len(scenes) < 5:
        raise ValueError("At least 5 scenes required")

    narration = str(plan.get("narration", "")).strip()
    if not narration:
        raise ValueError("Narration is empty")

    audio_raw = os.path.join(output_dir, "voice_raw.mp3")
    audio_final = os.path.join(output_dir, "voice.mp3")
    asyncio.run(create_voice(narration, audio_raw))
    actual_duration = fit_audio(audio_raw, audio_final, target_seconds)

    print("\nTarget media requirement: 7 unique images + 4 unique videos")
    images, videos, credits = collect_media_assets(
        scenes, output_dir, required_images=7, required_videos=4
    )

    print(f"\nMedia collected: {len(images)} images, {len(videos)} videos")
    if len(images) < 7 or len(videos) < 4:
        raise RuntimeError(
            "Not enough real media assets. Required: 7 unique images and 4 unique "
            f"videos; found {len(images)} images and {len(videos)} videos. "
            "Video was stopped instead of creating fake/plain fallback visuals. "
            "Try a broader visual_query in the plan or run again later."
        )

    # Mix all 11 assets into the timeline, with video clips interspersed.
    timeline = []
    for i in range(7):
        timeline.append(("image", images[i], i))
    for i in range(4):
        timeline.insert(min((i + 1) * 2, len(timeline)), ("video", videos[i], min(i * 2, len(scenes) - 1)))

    segment_duration = actual_duration / len(timeline)
    clip_paths = []

    for index, (media_type, asset, scene_index) in enumerate(timeline):
        print(f"Rendering visual {index + 1}/{len(timeline)}: {media_type}")
        caption = _scene_caption(scenes, scene_index)
        caption_path = os.path.join(output_dir, f"caption_{index+1}.png")
        clip_path = os.path.join(output_dir, f"visual_{index+1}.mp4")
        make_caption(caption, caption_path)

        if media_type == "image":
            create_image_scene(asset["path"], caption_path, clip_path, segment_duration)
        else:
            create_video_scene(asset["path"], caption_path, clip_path, segment_duration)
        clip_paths.append(clip_path)

    concat_file = os.path.join(output_dir, "concat.txt")
    with open(concat_file, "w", encoding="utf-8") as file:
        for path in clip_paths:
            safe_path = os.path.abspath(path).replace("'", "'\\''")
            file.write(f"file '{safe_path}'\n")

    silent_video = os.path.join(output_dir, "silent_video.mp4")
    run_command([
        "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", concat_file,
        "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
        "-r", str(FPS), silent_video
    ])

    final_path = os.path.join(output_dir, "autofact_short.mp4")
    run_command([
        "ffmpeg", "-y", "-i", silent_video, "-i", audio_final,
        "-map", "0:v:0", "-map", "1:a:0",
        "-c:v", "copy", "-c:a", "aac", "-b:a", "160k",
        "-t", f"{actual_duration:.3f}", "-movflags", "+faststart", final_path
    ])

    # Save credits alongside the rendered video.
    credits_path = os.path.join(output_dir, "media_credits.txt")
    with open(credits_path, "w", encoding="utf-8") as file:
        file.write("\n".join(dict.fromkeys(credits)))

    print("\nVideo created:", final_path)
    print("Duration:", round(get_audio_duration(final_path), 2), "seconds")
    print("Credits saved:", credits_path)
    return final_path, list(dict.fromkeys(credits))
