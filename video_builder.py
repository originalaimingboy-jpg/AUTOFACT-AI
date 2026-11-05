import os
import re
import math
import random
import subprocess

import edge_tts

from PIL import Image, ImageDraw, ImageFont

from commons import (
    find_commons_image,
    find_commons_video
)


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
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )

    if result.returncode != 0:
        raise RuntimeError(
            "FFmpeg error:\n" + result.stderr[-3000:]
        )


def get_font(size):
    for path in FONT_PATHS:
        if os.path.exists(path):
            return ImageFont.truetype(path, size)

    return ImageFont.load_default()


def safe_name(text):
    text = re.sub(r"[^a-zA-Z0-9_-]", "_", text)
    return text[:45]


def wrap_text(text, max_chars=23):
    words = str(text).split()
    lines = []
    current = ""

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
    image = Image.new(
        "RGBA",
        (WIDTH, 350),
        (0, 0, 0, 0)
    )

    draw = ImageDraw.Draw(image)
    font = get_font(66)

    lines = wrap_text(text)
    line_height = 82

    total_height = len(lines) * line_height
    y = (350 - total_height) // 2

    for line in lines:
        bbox = draw.textbbox(
            (0, 0),
            line,
            font=font,
            stroke_width=3
        )

        text_width = bbox[2] - bbox[0]
        x = (WIDTH - text_width) // 2

        draw.text(
            (x, y),
            line,
            font=font,
            fill="white",
            stroke_width=4,
            stroke_fill="black"
        )

        y += line_height

    image.save(output_path)


def make_fallback_background(output_path, seed):
    random.seed(seed)

    image = Image.new(
        "RGB",
        (WIDTH, HEIGHT),
        (12, 17, 30)
    )

    draw = ImageDraw.Draw(image)

    for i in range(30):
        x = random.randint(0, WIDTH)
        y = random.randint(0, HEIGHT)
        radius = random.randint(20, 140)

        draw.ellipse(
            (
                x - radius,
                y - radius,
                x + radius,
                y + radius
            ),
            fill=(
                random.randint(15, 45),
                random.randint(25, 65),
                random.randint(50, 110)
            )
        )

    image.save(output_path)


async def create_voice(text, output_path, target_seconds):
    communicate = edge_tts.Communicate(
        text=text,
        voice=VOICE_NAME,
        rate="+0%",
        volume="+0%"
    )

    await communicate.save(output_path)

    return output_path


def get_audio_duration(path):
    result = subprocess.run(
        [
            "ffprobe",
            "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            path
        ],
        capture_output=True,
        text=True
    )

    return float(result.stdout.strip())


def fit_audio(input_path, output_path, target_seconds):
    duration = get_audio_duration(input_path)

    if duration <= 0:
        raise RuntimeError("Audio duration invalid")

    # Keep voice speed within a reasonable range.
    speed = duration / target_seconds
    speed = max(0.88, min(1.15, speed))

    run_command([
        "ffmpeg",
        "-y",
        "-i", input_path,
        "-filter:a", f"atempo={speed:.4f}",
        "-c:a", "libmp3lame",
        "-q:a", "3",
        output_path
    ])

    return get_audio_duration(output_path)


def create_image_scene(image_path, caption_path, output_path, duration):
    frames = max(1, int(duration * FPS))

    zoom_direction = random.choice(["in", "out"])

    if zoom_direction == "in":
        zoom = (
            "z='min(zoom+0.0008,1.12)':"
            "x='iw/2-(iw/zoom/2)':"
            "y='ih/2-(ih/zoom/2)'"
        )
    else:
        zoom = (
            "z='if(eq(on,1),1.12,max(zoom-0.0008,1.0))':"
            "x='iw/2-(iw/zoom/2)':"
            "y='ih/2-(ih/zoom/2)'"
        )

    filter_complex = (
        f"scale=1200:2134:force_original_aspect_ratio=increase,"
        f"crop=1200:2134,"
        f"zoompan={zoom}:d={frames}:s={WIDTH}x{HEIGHT}:fps={FPS},"
        f"setsar=1,"
        f"eq=contrast=1.08:saturation=1.12,"
        f"format=yuv420p"
    )

    run_command([
        "ffmpeg",
        "-y",
        "-loop", "1",
        "-i", image_path,
        "-loop", "1",
        "-i", caption_path,
        "-filter_complex",
        (
            f"[0:v]{filter_complex}[base];"
            f"[base][1:v]overlay=0:1200:shortest=1[out]"
        ),
        "-map", "[out]",
        "-t", str(duration),
        "-r", str(FPS),
        "-an",
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-pix_fmt", "yuv420p",
        output_path
    ])


def create_video_scene(video_path, caption_path, output_path, duration):
    run_command([
        "ffmpeg",
        "-y",
        "-stream_loop", "-1",
        "-i", video_path,
        "-loop", "1",
        "-i", caption_path,
        "-filter_complex",
        (
            f"[0:v]scale=1200:2134:force_original_aspect_ratio=increase,"
            f"crop=1200:2134,"
            f"fps={FPS},"
            f"eq=contrast=1.08:saturation=1.12,"
            f"setsar=1[base];"
            f"[base][1:v]overlay=0:1200:shortest=1[out]"
        ),
        "-map", "[out]",
        "-t", str(duration),
        "-r", str(FPS),
        "-an",
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-pix_fmt", "yuv420p",
        output_path
    ])


def build_video(plan, output_dir, voice_name=VOICE_NAME, target_seconds=DEFAULT_SECONDS):
    os.makedirs(output_dir, exist_ok=True)

    target_seconds = max(
        MIN_SECONDS,
        min(MAX_SECONDS, int(target_seconds))
    )

    global VOICE_NAME
    VOICE_NAME = voice_name

    scenes = plan.get("scenes", [])

    if not scenes:
        raise ValueError("Plan has no scenes")

    narration = plan.get("narration", "").strip()

    if not narration:
        raise ValueError("Narration is empty")

    # Split narration into scene segments.
    scene_count = min(7, max(5, len(scenes)))
    scenes = scenes[:scene_count]

    if len(scenes) < 5:
        raise ValueError("At least 5 scenes required")

    audio_raw = os.path.join(output_dir, "voice_raw.mp3")
    audio_final = os.path.join(output_dir, "voice.mp3")

    import asyncio

    asyncio.run(
        create_voice(
            narration,
            audio_raw,
            target_seconds
        )
    )

    actual_audio_duration = fit_audio(
        audio_raw,
        audio_final,
        target_seconds
    )

    scene_duration = actual_audio_duration / len(scenes)

    credits = []
    clip_paths = []

    for index, scene in enumerate(scenes):
        print(f"\nBuilding scene {index + 1}/{len(scenes)}")

        visual_query = (
            scene.get("visual_query")
            or scene.get("search_query")
            or scene.get("caption")
            or scene.get("title")
            or "science technology"
        )

        caption = (
            scene.get("caption")
            or scene.get("title")
            or ""
        )

        base_name = f"scene_{index + 1}"
        image_path = os.path.join(
            output_dir,
            base_name + ".jpg"
        )
        video_path = os.path.join(
            output_dir,
            base_name + ".mp4"
        )
        caption_path = os.path.join(
            output_dir,
            base_name + "_caption.png"
        )
        clip_path = os.path.join(
            output_dir,
            base_name + "_clip.mp4"
        )

        make_caption(caption, caption_path)

        selected_path = None
        selected_type = None
        credit = None

        # Try a video first.
        selected_path, credit = find_commons_video(
            visual_query,
            video_path
        )

        if selected_path:
            selected_type = "video"
        else:
            # If video unavailable, use a photo with motion.
            selected_path, credit = find_commons_image(
                visual_query,
                image_path
            )

            if selected_path:
                selected_type = "image"

        if selected_type == "video":
            create_video_scene(
                selected_path,
                caption_path,
                clip_path,
                scene_duration
            )

        elif selected_type == "image":
            create_image_scene(
                selected_path,
                caption_path,
                clip_path,
                scene_duration
            )

        else:
            print("No Commons asset found; generating fallback visual.")

            fallback_path = os.path.join(
                output_dir,
                base_name + "_fallback.jpg"
            )

            make_fallback_background(
                fallback_path,
                index + 100
            )

            create_image_scene(
                fallback_path,
                caption_path,
                clip_path,
                scene_duration
            )

        if credit:
            credits.append(credit)

        clip_paths.append(clip_path)

    # Concatenate all clips.
    concat_file = os.path.join(
        output_dir,
        "concat.txt"
    )

    with open(concat_file, "w", encoding="utf-8") as file:
        for path in clip_paths:
            safe_path = os.path.abspath(path).replace("'", "'\\''")
            file.write(f"file '{safe_path}'\n")

    silent_video = os.path.join(
        output_dir,
        "silent_video.mp4"
    )

    run_command([
        "ffmpeg",
        "-y",
        "-f", "concat",
        "-safe", "0",
        "-i", concat_file,
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-pix_fmt", "yuv420p",
        "-r", str(FPS),
        silent_video
    ])

    final_path = os.path.join(
        output_dir,
        "autofact_short.mp4"
    )

    run_command([
        "ffmpeg",
        "-y",
        "-i", silent_video,
        "-i", audio_final,
        "-map", "0:v:0",
        "-map", "1:a:0",
        "-c:v", "copy",
        "-c:a", "aac",
        "-b:a", "160k",
        "-t", str(actual_audio_duration),
        "-movflags", "+faststart",
        final_path
    ])

    print("\nVideo created:", final_path)
    print("Duration:", round(get_audio_duration(final_path), 2), "seconds")

    return final_path, credits