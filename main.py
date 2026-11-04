
import json
import os
import re
import sys
from pathlib import Path

from google import genai
from google.genai import types

from research import get_wikipedia_context
from video_builder import build_video
from youtube_upload import upload_video

ROOT = Path(__file__).resolve().parent
OUTPUT_DIR = Path(os.getenv("OUTPUT_DIR", "work"))
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
HISTORY_PATH = ROOT / "used_topics.json"


def require_env(name):
    value = os.getenv(name)
    if not value:
        raise RuntimeError(
            f"Missing required GitHub secret/environment variable: {name}"
        )
    return value


def load_history():
    try:
        return json.loads(
            HISTORY_PATH.read_text(encoding="utf-8")
        ).get("topics", [])
    except Exception:
        return []


def clean_json(text):
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    return json.loads(text)


def ask_gemini(client, model, prompt):
    print(f"Requesting Gemini model: {model}", flush=True)

    response = client.models.generate_content(
        model=model,
        contents=prompt,
        config=types.GenerateContentConfig(
            temperature=0.75,
            response_mime_type="application/json",
        ),
    )

    if not response.text:
        raise RuntimeError("Gemini returned an empty response.")

    return clean_json(response.text)


def create_plan(client, model, history, target_seconds):
    recent = [x.get("topic", "") for x in history[-40:]]

    prompt = f"""
You are AUTOFACT AI, a responsible Hinglish YouTube Shorts editorial team.
Create ONE original 45–60 second short for a general audience.
Choose a topic from evergreen science, space, history, geography, archaeology,
or a non-graphic mystery. Avoid violent instructions, weapons, crime tutorials,
self-harm, medical advice, financial advice, political persuasion, graphic details,
celebrity gossip, conspiracy claims presented as fact, and unverified sensational claims.

Avoid these previously used topics:
{json.dumps(recent, ensure_ascii=False)}

You must return valid JSON only with this exact schema:
{{
 "topic": "short topic name",
 "topic_slug": "lowercase-hyphenated-topic",
 "wikipedia_search": "a concise English Wikipedia search phrase",
 "title": "YouTube title under 90 characters, Hinglish or Hindi",
 "description_intro": "2-3 original Hinglish sentences, no unsupported claims",
 "hashtags": ["#Shorts", "#Mystery", "#Facts"],
 "tags": ["tag1", "tag2"],
 "narration": "Natural conversational Hinglish voiceover, 110-145 words maximum",
 "style": "a short description of the visual style selected for this topic",
 "scenes": [
   {{
     "visual_query": "English image search query, 3-6 words",
     "caption": "short Hinglish on-screen caption, max 7 words"
   }}
 ]
}}

Rules:
- Exactly 5 scenes.
- Target narration duration about {target_seconds} seconds.
- Hook in first sentence; end with a satisfying takeaway.
- Be accurate and conservative. Do not invent dates, statistics, quotes, named sources or events.
- Choose a topic that can be supported by a well-known Wikipedia article.
- The narration must not mention that it was written by AI.
- The visuals should match the topic, but must not imply that an illustrative image is actual archival evidence.
- Use Roman-script Hinglish for narration and captions so the Hindi voice can read it.
"""
    return ask_gemini(client, model, prompt)


def refine_plan(client, model, plan, wiki_context, target_seconds):
    prompt = f"""
You are a cautious fact-checking editor. Use only the source context below for
specific factual claims. If the source context does not support a detail, remove
or soften it. Do not add new facts. Keep the narration in natural Roman-script
Hinglish, approximately {target_seconds} seconds, and keep exactly five scenes.
Return the same JSON schema as the supplied plan.

SOURCE CONTEXT:
{wiki_context[:9000]}

DRAFT:
{json.dumps(plan, ensure_ascii=False)}
"""
    return ask_gemini(client, model, prompt)


def main():
    api_key = require_env("GEMINI_API_KEY")
    model = os.getenv("GEMINI_MODEL") or "gemini-3.8-flash"
    target_seconds = int(os.getenv("VIDEO_SECONDS", "52"))
    voice = os.getenv("VOICE_NAME", "hi-IN-MadhurNeural")

    # More attempts for temporary Gemini server errors.
    retry_options = types.HttpRetryOptions(
        attempts=7,
        initial_delay=2,
        max_delay=60,
        exp_base=2,
        jitter=1,
        http_status_codes=[408, 429, 500, 502, 503, 504],
    )

    client = genai.Client(
        api_key=api_key,
        http_options=types.HttpOptions(
            retry_options=retry_options
        ),
    )

    history = load_history()

    print("STEP 1: Creating video plan...", flush=True)
    plan = create_plan(client, model, history, target_seconds)

    if not isinstance(plan.get("scenes"), list) or len(plan["scenes"]) != 5:
        raise RuntimeError("Gemini returned an invalid scene plan.")

    if not plan.get("narration") or not plan.get("topic"):
        raise RuntimeError("Gemini returned an incomplete plan.")

    print("STEP 2: Getting Wikipedia research...", flush=True)
    wiki = get_wikipedia_context(
        plan.get("wikipedia_search", plan["topic"])
    )

    if not wiki:
        raise RuntimeError(
            "Could not retrieve Wikipedia source context; "
            "stopping rather than publishing an unchecked topic."
        )

    print("STEP 3: Fact-checking the script...", flush=True)
    plan = refine_plan(client, model, plan, wiki, target_seconds)

    if len(plan.get("scenes", [])) != 5:
        raise RuntimeError(
            "Fact-checking pass returned an invalid scene plan."
        )

    print("STEP 4: Building video...", flush=True)
    video_path, credits = build_video(plan, OUTPUT_DIR, voice)

    source_line = next(
        (line for line in wiki.splitlines() if line.startswith("URL:")),
        ""
    )

    description = (
        plan.get("description_intro", "").strip()
        + (
            "\n\nResearch context: " + source_line[4:].strip()
            if source_line else ""
        )
        + "\n\nVisual credits and licences:\n"
        + "\n".join(credits)
        + "\n\nPhotos and media via Wikimedia Commons; "
        "see each linked file page for its licence.\n"
        + "This video uses illustrative visuals and AI-generated narration."
    )

    hashtags = plan.get("hashtags", ["#Shorts", "#Facts"])
    description += "\n\n" + " ".join(hashtags[:8])

    title = plan.get("title", plan["topic"])[:95]

    tags = [str(t)[:30] for t in plan.get("tags", [])[:15]]
    tags += ["Shorts", "Hinglish", "Facts"]

    print("STEP 5: Uploading to YouTube...", flush=True)

    video_id = upload_video(
        video_path=video_path,
        title=title,
        description=description[:4900],
        tags=tags,
    )

    history.append({
        "topic": plan["topic"],
        "slug": plan.get("topic_slug", ""),
        "video_id": video_id,
    })

    HISTORY_PATH.write_text(
        json.dumps(
            {"topics": history[-200:]},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    print(
        f"SUCCESS: https://www.youtube.com/shorts/{video_id}",
        flush=True
    )


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"AUTOFACT AI FAILED: {exc}", file=sys.stderr)
        raise
