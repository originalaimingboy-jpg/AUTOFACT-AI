
import asyncio
import os
import subprocess
import textwrap
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
import edge_tts

from commons import find_commons_image


WIDTH, HEIGHT, FPS = 1080, 1920, 25
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
CAPTION_SIZE = 43
TARGET_SECONDS = 52.0
MIN_VOICE_SECONDS = 40.0
MAX_VOICE_SECONDS = 58.0


def run(cmd):
    subprocess.run(
        cmd,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )


def get_audio_duration(audio):
    probe = subprocess.check_output([
        "ffprobe",
        "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(audio)
    ], text=True).strip()

    return float(probe)


def load_font(size):
    try:
        return ImageFont.truetype(FONT, size)
    except Exception:
        return ImageFont.load_default()


def make_fallback(path, caption, index):
    img = Image.new(
        "RGB",
        (WIDTH, HEIGHT),
        (13 + index * 3, 12, 30 + index * 5)
    )

    draw = ImageDraw.Draw(img)

    for y in range(HEIGHT):
        shade = int(20 + 35 * y / HEIGHT)
        draw.line(
            (0, y, WIDTH, y),
            fill=(shade // 2, shade // 3, shade)
        )

    font = load_font(48)

    lines = textwrap.wrap(caption[:90], width=25)[:3]
    line_height = 65
    total_height = len(lines) * line_height
    start_y = (HEIGHT - total_height) // 2

    for i, line in enumerate(lines):
        box = draw.textbbox((0, 0), line, font=font)
        text_width = box[2] - box[0]

        draw.text(
            ((WIDTH - text_width) / 2, start_y + i * line_height),
            line,
            font=font,
            fill="white"
        )

    img.save(path, quality=92)


def fit_image(source, destination):
    with Image.open(source) as im:
        im = im.convert("RGB")

        target_ratio = WIDTH / HEIGHT
        ratio = im.width / im.height

        if ratio > target_ratio:
            new_w = int(im.height * target_ratio)
            left = (im.width - new_w) // 2

            im = im.crop((
                left,
                0,
                left + new_w,
                im.height
            ))

        else:
            new_h = int(im.width / target_ratio)
            top = (im.height - new_h) // 2

            im = im.crop((
                0,
                top,
                im.width,
                top + new_h
            ))

        im = im.resize(
            (WIDTH, HEIGHT),
            Image.Resampling.LANCZOS
        )

        im.save(destination, quality=94)


def create_caption_image(caption, path):
    overlay = Image.new(
        "RGBA",
        (WIDTH, HEIGHT),
        (0, 0, 0, 0)
    )

    draw = ImageDraw.Draw(overlay)
    font = load_font(CAPTION_SIZE)

    lines = textwrap.wrap(
        str(caption).strip()[:100],
        width=27
    )[:2]

    if not lines:
        lines = [""]

    line_height = 62
    padding_x = 35
    padding_y = 22

    dimensions = [
        draw.textbbox((0, 0), line, font=font)
        for line in lines
    ]

    max_width = max(
        box[2] - box[0]
        for box in dimensions
    )

    total_height = len(lines) * line_height

    box_width = max_width + padding_x * 2
    box_height = total_height + padding_y * 2

    box_x = (WIDTH - box_width) // 2
    box_y = int(HEIGHT * 0.72)

    draw.rounded_rectangle(
        (
            box_x,
            box_y,
            box_x + box_width,
            box_y + box_height
        ),
        radius=22,
        fill=(0, 0, 0, 175)
    )

    for index, line in enumerate(lines):
        bbox = draw.textbbox((0, 0), line, font=font)
        text_width = bbox[2] - bbox[0]

        x = (WIDTH - text_width) // 2
        y = box_y + padding_y + index * line_height

        draw.text(
            (x, y),
            line,
            font=font,
            fill=(255, 255, 255, 255),
            stroke_width=1,
            stroke_fill=(0, 0, 0, 180)
        )

    overlay.save(path)


async def make_voice(text, path, voice, rate):
    communicate = edge_tts.Communicate(
        text=text,
        voice=voice,
        rate=f"{rate:+d}%",
        volume="+0%"
    )

    await communicate.save(str(path))


def create_fitted_voice(narration, audio, voice):
    rate = 8
    target_seconds = TARGET_SECONDS
    duration = 0

    for attempt in range(5):
        if os.path.exists(audio):
            os.remove(audio)

        asyncio.run(
            make_voice(
                narration,
                audio,
                voice,
                rate
            )
        )

        duration = get_audio_duration(audio)

        print(
            f"Voice attempt {attempt + 1}: "
            f"{duration:.1f}s at rate {rate:+d}%",
            flush=True
        )

        if MIN_VOICE_SECONDS <= duration <= MAX_VOICE_SECONDS:
            print("Voice duration accepted.", flush=True)
            return duration

        suggested_rate = round(
            (1 + rate / 100)
            * (duration / target_seconds)
            * 100 - 100
        )

        rate = max(-5, min(35, suggested_rate))

    if MIN_VOICE_SECONDS <= duration <= 60:
        print(
            "Using final voice duration.",
            flush=True
        )
        return duration

    raise RuntimeError(
        f"Voice duration could not be fitted: {duration:.1f}s"
    )


def build_scene(still, caption, clip, duration, index, work):
    caption_path = work / f"caption_overlay_{index}.png"

    create_caption_image(
        caption,
        caption_path
    )

    frames = max(1, int(duration * FPS))

    zoom_direction = index % 2

    if zoom_direction == 0:
        zoom = "min(zoom+0.0008,1.13)"
    else:
        zoom = "max(zoom-0.0005,1.0)"

    vf = (
        f"zoompan=z='{zoom}':"
        f"d={frames}:"
        f"x='iw/2-(iw/zoom/2)':"
        f"y='ih/2-(ih/zoom/2)':"
        f"s={WIDTH}x{HEIGHT}:"
        f"fps={FPS},"
        "eq=saturation=1.12:contrast=1.04:brightness=0.01,"
        "format=yuv420p"
    )

    run([
        "ffmpeg",
        "-y",
        "-loop", "1",
        "-i", str(still),
        "-i", str(caption_path),
        "-filter_complex",
        f"[0:v]{vf}[base];"
        "[base][1:v]overlay=0:0:shortest=1[out]",
        "-map", "[out]",
        "-t", f"{duration:.3f}",
        "-r", str(FPS),
        "-an",
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-crf", "24",
        "-pix_fmt", "yuv420p",
        str(clip)
    ])


def build_video(plan, output_dir, voice):
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    work = output_dir / "build"
    work.mkdir(parents=True, exist_ok=True)

    narration = plan["narration"].strip()
    audio = output_dir / "narration.mp3"

    audio_seconds = create_fitted_voice(
        narration,
        audio,
        voice
    )

    total_seconds = audio_seconds
    scenes = plan["scenes"]

    if len(scenes) != 5:
        raise RuntimeError(
            "Video builder requires exactly 5 scenes."
        )

    scene_seconds = total_seconds / len(scenes)

    clips = []
    credits = []

    for index, scene in enumerate(scenes):
        print(
            f"Building scene {index + 1}/{len(scenes)}...",
            flush=True
        )

        raw = work / f"raw_{index}.jpg"
        still = work / f"scene_{index}.jpg"
        clip = work / f"clip_{index}.mp4"

        query = scene.get(
            "visual_query",
            plan["topic"]
        )

        found, credit = find_commons_image(
            query,
            raw
        )

        if found and Path(found).exists():
            fit_image(found, still)

            if credit:
                credits.append(credit)

        else:
            make_fallback(
                still,
                scene.get("caption", plan["topic"]),
                index
            )

            credits.append(
                f"Scene {index + 1}: generated fallback illustration."
            )

        caption = scene.get(
            "caption",
            plan["topic"]
        )

        build_scene(
            still,
            caption,
            clip,
            scene_seconds,
            index,
            work
        )

        clips.append(clip)

    concat_file = work / "concat.txt"

    concat_file.write_text(
        "\n".join(
            f"file '{clip.resolve()}'"
            for clip in clips
        ),
        encoding="utf-8"
    )

    joined = work / "joined.mp4"

    run([
        "ffmpeg",
        "-y",
        "-f", "concat",
        "-safe", "0",
        "-i", str(concat_file),
        "-c", "copy",
        str(joined)
    ])

    final_path = output_dir / "autofact_short.mp4"

    run([
        "ffmpeg",
        "-y",
        "-i", str(joined),
        "-i", str(audio),
        "-map", "0:v:0",
        "-map", "1:a:0",
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-crf", "24",
        "-c:a", "aac",
        "-b:a", "128k",
        "-t", f"{total_seconds:.2f}",
        "-pix_fmt", "yuv420p",
        "-movflags", "+faststart",
        str(final_path)
    ])

    print(
        f"Final video ready: {final_path}",
        flush=True
    )

    return final_path, credits
