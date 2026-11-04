
import asyncio
import os
import subprocess
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
import edge_tts

from commons import find_commons_image

WIDTH, HEIGHT, FPS = 1080, 1920, 25
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"


def run(cmd):
    subprocess.run(cmd, check=True)


def get_audio_duration(audio):
    probe = subprocess.check_output([
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(audio)
    ], text=True).strip()
    return float(probe)


def make_fallback(path, caption, index):
    img = Image.new("RGB", (WIDTH, HEIGHT), (13 + index * 3, 12, 30 + index * 5))
    draw = ImageDraw.Draw(img)

    for y in range(HEIGHT):
        shade = int(20 + 35 * y / HEIGHT)
        draw.line((0, y, WIDTH, y), fill=(shade // 2, shade // 3, shade))

    try:
        font = ImageFont.truetype(FONT, 54)
    except Exception:
        font = ImageFont.load_default()

    text = caption[:42]
    box = draw.textbbox((0, 0), text, font=font)
    draw.text(
        ((WIDTH - (box[2] - box[0])) / 2, HEIGHT * 0.44),
        text, font=font, fill="white"
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
            im = im.crop((left, 0, left + new_w, im.height))
        else:
            new_h = int(im.width / target_ratio)
            top = (im.height - new_h) // 2
            im = im.crop((0, top, im.width, top + new_h))

        im = im.resize((WIDTH, HEIGHT), Image.Resampling.LANCZOS)
        im.save(destination, quality=92)


async def make_voice(text, path, voice, rate):
    communicate = edge_tts.Communicate(
        text=text,
        voice=voice,
        rate=f"{rate:+d}%",
        volume="+0%"
    )
    await communicate.save(str(path))


def create_fitted_voice(narration, audio, voice):
    rate = 3
    target_seconds = 55.0

    for attempt in range(4):
        asyncio.run(make_voice(narration, audio, voice, rate))
        duration = get_audio_duration(audio)

        print(
            f"Voice attempt {attempt + 1}: "
            f"{duration:.1f}s at rate {rate:+d}%"
        )

        if 40 <= duration <= 60:
            print("Narration duration accepted.")
            return duration

        # Calculate a better speech rate for the next attempt.
        # Higher rate = faster speech; lower rate = slower speech.
        suggested_rate = round(
            (1 + rate / 100) * (duration / target_seconds) * 100 - 100
        )

        rate = max(-15, min(25, suggested_rate))

    raise RuntimeError(
        f"Could not fit narration into 40–60 seconds after 4 attempts. "
        f"Last duration: {duration:.1f}s. "
        "Try generating a shorter narration."
    )


def build_video(plan, output_dir, voice):
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    work = output_dir / "build"
    work.mkdir(exist_ok=True)

    narration = plan["narration"].strip()
    audio = output_dir / "narration.mp3"

    # Automatically adjust voice speed to fit the target duration.
    audio_seconds = create_fitted_voice(narration, audio, voice)

    total_seconds = audio_seconds + 0.15
    scene_seconds = total_seconds / 5

    clips, credits = [], []

    for i, scene in enumerate(plan["scenes"]):
        raw = work / f"raw_{i}.jpg"
        still = work / f"scene_{i}.jpg"

        query = scene.get("visual_query", plan["topic"])
        found, credit = find_commons_image(query, raw)

        if found:
            fit_image(found, still)
            credits.append(credit)
        else:
            make_fallback(
                still,
                scene.get("caption", plan["topic"]),
                i
            )
            credits.append(
                f"Scene {i+1}: generated gradient illustration "
                "(no external image)."
            )

        caption_file = work / f"caption_{i}.txt"
        caption_file.write_text(
            scene.get("caption", "")[:70],
            encoding="utf-8"
        )

        clip = work / f"clip_{i}.mp4"
        frames = int(scene_seconds * FPS)

        vf = (
            f"scale={WIDTH}:{HEIGHT},"
            f"zoompan=z='min(zoom+0.0007,1.12)':d={frames}:"
            f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
            f"s={WIDTH}x{HEIGHT}:fps={FPS},"
            f"drawtext=fontfile={FONT}:textfile={caption_file}:"
            "fontcolor=white:fontsize=54:line_spacing=12:"
            "box=1:boxcolor=black@0.58:boxborderw=24:"
            "x=(w-text_w)/2:y=h*0.76,"
            "format=yuv420p"
        )

        run([
            "ffmpeg", "-y",
            "-loop", "1", "-i", str(still),
            "-t", f"{scene_seconds:.3f}",
            "-vf", vf,
            "-r", str(FPS),
            "-an",
            "-c:v", "libx264",
            "-preset", "ultrafast",
            "-crf", "25",
            str(clip)
        ])

        clips.append(clip)

    concat_file = work / "concat.txt"
    concat_file.write_text(
        "\n".join(f"file '{p.resolve()}'" for p in clips),
        encoding="utf-8"
    )

    joined = work / "joined.mp4"

    run([
        "ffmpeg", "-y",
        "-f", "concat",
        "-safe", "0",
        "-i", str(concat_file),
        "-c", "copy",
        str(joined)
    ])

    final_path = output_dir / "autofact_short.mp4"

    run([
        "ffmpeg", "-y",
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

    return final_path, credits
