
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


# ============================================================
# AUTOFACT AI - CONFIGURATION
# ============================================================

ROOT = Path(__file__).resolve().parent
OUTPUT_DIR = Path(os.getenv("OUTPUT_DIR", "work"))
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

HISTORY_PATH = ROOT / "used_topics.json"

DEFAULT_MODEL = "gemini-3.8-flash"
FALLBACK_MODEL = os.getenv("GEMINI_FALLBACK_MODEL", "").strip()

MAX_HISTORY = 200
TARGET_SECONDS = int(os.getenv("VIDEO_SECONDS", "52"))
VOICE_NAME = os.getenv("VOICE_NAME", "hi-IN-MadhurNeural")


# ============================================================
# ENVIRONMENT
# ============================================================

def require_env(name):
    value = os.getenv(name)

    if not value:
        raise RuntimeError(
            f"Missing required GitHub secret/environment variable: {name}"
        )

    return value


# ============================================================
# HISTORY MANAGEMENT
# ============================================================

def load_history():
    try:
        data = json.loads(
            HISTORY_PATH.read_text(encoding="utf-8")
        )

        return data.get("topics", [])

    except (FileNotFoundError, json.JSONDecodeError):
        return []

    except Exception as exc:
        print(f"History warning: {exc}", flush=True)
        return []


def save_history(history):
    data = {
        "topics": history[-MAX_HISTORY:]
    }

    HISTORY_PATH.write_text(
        json.dumps(
            data,
            ensure_ascii=False,
            indent=2
        ),
        encoding="utf-8"
    )


# ============================================================
# JSON CLEANER
# ============================================================

def clean_json(text):
    if not text:
        raise RuntimeError("Gemini returned an empty response.")

    text = text.strip()

    text = re.sub(
        r"^```(?:json)?\s*",
        "",
        text
    )

    text = re.sub(
        r"\s*```$",
        "",
        text
    )

    return json.loads(text)


# ============================================================
# GEMINI MODEL SYSTEM
# ============================================================

def ask_gemini(client, model, prompt):

    models_to_try = [model]

    if FALLBACK_MODEL and FALLBACK_MODEL != model:
        models_to_try.append(FALLBACK_MODEL)

    last_error = None

    for current_model in models_to_try:

        try:
            print(
                f"Trying Gemini model: {current_model}",
                flush=True
            )

            response = client.models.generate_content(
                model=current_model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    temperature=0.75,
                    response_mime_type="application/json",
                ),
            )

            if not response.text:
                raise RuntimeError(
                    "Gemini returned an empty response."
                )

            result = clean_json(response.text)

            print(
                f"Gemini success: {current_model}",
                flush=True
            )

            return result

        except Exception as exc:

            last_error = exc
            error_text = str(exc)

            print(
                f"Model failed: {current_model}",
                flush=True
            )

            print(
                f"Reason: {error_text}",
                flush=True
            )

            temporary_errors = [
                "503",
                "502",
                "500",
                "504",
                "429",
                "UNAVAILABLE",
                "RESOURCE_EXHAUSTED"
            ]

            if not any(
                code in error_text
                for code in temporary_errors
            ):
                raise

    raise RuntimeError(
        f"All configured Gemini models failed. Last error: {last_error}"
    )


# ============================================================
# CONTENT PLANNER
# ============================================================

def create_plan(client, model, history, target_seconds):

    recent_topics = [
        item.get("topic", "")
        for item in history[-40:]
    ]

    prompt = f"""
You are AUTOFACT AI.

You are an intelligent YouTube Shorts production team:
- Creative director
- Researcher
- Hinglish scriptwriter
- Documentary storyteller
- Visual planner

Your job is to create ONE highly engaging, accurate,
original YouTube Short.

TARGET:
- Duration: approximately {target_seconds} seconds
- Language: natural Roman-script Hinglish
- Audience: general Indian audience
- Voice: conversational male Hindi narration

TOPIC CATEGORIES:
Science, space, geography, history, archaeology,
technology, nature, interesting discoveries,
and non-graphic mysteries.

Avoid:
- Graphic violence
- Weapon instructions
- Crime tutorials
- Self-harm
- Medical advice
- Financial advice
- Political persuasion
- Unverified conspiracy claims
- Celebrity gossip
- Sensational misinformation

PREVIOUS TOPICS:
{json.dumps(recent_topics, ensure_ascii=False)}

Never repeat an already covered topic.

STORYTELLING RULES:

1. Start with a powerful curiosity-based hook.
2. Do not waste time with greetings or introductions.
3. Every sentence should reveal something interesting.
4. Use simple conversational Hinglish.
5. Avoid robotic or textbook-style narration.
6. Build curiosity progressively.
7. Use short sentences that sound natural when spoken.
8. End with a memorable takeaway.
9. Never invent statistics, dates, quotations or events.
10. Do not reveal that AI wrote the script.

VISUAL RULES:

- Exactly 5 scenes for compatibility with the current editor.
- Every scene must have a different visual subject or angle.
- Use cinematic documentary-style visual descriptions.
- Avoid generic images unrelated to the narration.
- Images must not be presented as authentic historical evidence
  unless their source actually establishes that.
- Captions must be short and readable.

RETURN VALID JSON ONLY:

{{
  "topic": "Topic name",
  "topic_slug": "lowercase-hyphenated-topic",
  "wikipedia_search": "English Wikipedia search phrase",
  "title": "Engaging YouTube title",
  "description_intro": "Two original Hinglish sentences",
  "hashtags": ["#Shorts", "#Facts"],
  "tags": ["science", "facts"],
  "narration": "Complete Hinglish voiceover script",
  "style": "Visual editing style",
  "scenes": [
    {{
      "visual_query": "Specific English visual search phrase",
      "caption": "Short Hinglish caption"
    }}
  ]
}}

IMPORTANT:
- Exactly five scenes.
- Narration should be approximately 110-135 words.
- Make the first 3 seconds especially interesting.
- Do not put the entire narration into captions.
- Return valid JSON only.
"""

    return ask_gemini(client, model, prompt)


# ============================================================
# FACT CHECKING
# ============================================================

def refine_plan(client, model, plan, wiki_context, target_seconds):

    prompt = f"""
You are AUTOFACT AI's strict fact-checking editor.

Your task is to improve the supplied video plan.

SOURCE:
Use ONLY the supplied Wikipedia context for specific factual claims.

RULES:

1. Remove unsupported claims.
2. Do not invent facts.
3. Preserve the original topic.
4. Keep natural Roman-script Hinglish.
5. Improve awkward sentences.
6. Keep the opening hook interesting.
7. Keep the ending satisfying.
8. Preserve the title and description meaning.
9. Keep exactly five scenes.
10. Keep visual descriptions relevant to the narration.
11. Do not add unsupported statistics or dates.
12. Do not make the narration unnecessarily slow or repetitive.

TARGET DURATION:
Approximately {target_seconds} seconds.

SOURCE CONTEXT:
{wiki_context[:9000]}

DRAFT PLAN:
{json.dumps(plan, ensure_ascii=False)}

Return the complete corrected JSON using exactly the same schema.
"""

    return ask_gemini(client, model, prompt)


# ============================================================
# PLAN VALIDATION
# ============================================================

def validate_plan(plan):

    if not isinstance(plan, dict):
        raise RuntimeError("Invalid plan format.")

    required_fields = [
        "topic",
        "title",
        "narration",
        "scenes"
    ]

    for field in required_fields:

        if not plan.get(field):
            raise RuntimeError(
                f"Missing required plan field: {field}"
            )

    if not isinstance(plan["scenes"], list):
        raise RuntimeError("Scenes must be a list.")

    if len(plan["scenes"]) != 5:
        raise RuntimeError(
            "Invalid scene count. Expected exactly 5."
        )

    for index, scene in enumerate(plan["scenes"]):

        if not isinstance(scene, dict):
            raise RuntimeError(
                f"Invalid scene at position {index + 1}"
            )

        if not scene.get("visual_query"):
            raise RuntimeError(
                f"Missing visual query in scene {index + 1}"
            )

        if not scene.get("caption"):
            raise RuntimeError(
                f"Missing caption in scene {index + 1}"
            )

    narration_words = len(
        plan["narration"].split()
    )

    if narration_words < 60:
        raise RuntimeError(
            "Narration is too short."
        )

    if narration_words > 160:
        raise RuntimeError(
            "Narration is too long."
        )

    print(
        f"Plan validated: {narration_words} narration words, "
        f"{len(plan['scenes'])} scenes.",
        flush=True
    )


# ============================================================
# DESCRIPTION GENERATOR
# ============================================================

def create_description(plan, wiki, credits):

    source_line = next(
        (
            line
            for line in wiki.splitlines()
            if line.startswith("URL:")
        ),
        ""
    )

    description = (
        plan.get("description_intro", "").strip()
    )

    if source_line:

        description += (
            "\n\nResearch context: "
            + source_line[4:].strip()
        )

    description += (
        "\n\nVisual credits and licences:\n"
        + "\n".join(credits)
    )

    description += (
        "\n\nPhotos and media via Wikimedia Commons; "
        "see each linked file page for its licence."
    )

    description += (
        "\nThis video uses illustrative visuals "
        "and AI-generated narration."
    )

    hashtags = plan.get(
        "hashtags",
        ["#Shorts", "#Facts"]
    )

    description += (
        "\n\n" + " ".join(
            str(tag) for tag in hashtags[:8]
        )
    )

    return description[:4900]


# ============================================================
# MAIN AUTOMATION
# ============================================================

def main():

    print("=" * 50)
    print("AUTOFACT AI STARTING")
    print("=" * 50)

    api_key = require_env("GEMINI_API_KEY")

    model = (
        os.getenv("GEMINI_MODEL")
        or DEFAULT_MODEL
    )

    print(
        f"Primary model: {model}",
        flush=True
    )

    if FALLBACK_MODEL:
        print(
            f"Backup model: {FALLBACK_MODEL}",
            flush=True
        )

    retry_options = types.HttpRetryOptions(
        attempts=7,
        initial_delay=2,
        max_delay=60,
        exp_base=2,
        jitter=1,
        http_status_codes=[
            408,
            429,
            500,
            502,
            503,
            504
        ],
    )

    client = genai.Client(
        api_key=api_key,
        http_options=types.HttpOptions(
            retry_options=retry_options
        ),
    )

    history = load_history()

    # STEP 1
    print("\nSTEP 1: Creating video plan...", flush=True)

    plan = create_plan(
        client,
        model,
        history,
        TARGET_SECONDS
    )

    validate_plan(plan)

    # STEP 2
    print("\nSTEP 2: Researching topic...", flush=True)

    wiki = get_wikipedia_context(
        plan.get(
            "wikipedia_search",
            plan["topic"]
        )
    )

    if not wiki:
        raise RuntimeError(
            "Wikipedia research failed. "
            "Stopping to avoid publishing unchecked content."
        )

    # STEP 3
    print("\nSTEP 3: Fact-checking script...", flush=True)

    plan = refine_plan(
        client,
        model,
        plan,
        wiki,
        TARGET_SECONDS
    )

    validate_plan(plan)

    # STEP 4
    print("\nSTEP 4: Building video...", flush=True)

    video_path, credits = build_video(
        plan,
        OUTPUT_DIR,
        VOICE_NAME
    )

    if not Path(video_path).exists():
        raise RuntimeError(
            "Video builder did not create the expected video file."
        )

    print(
        f"Video created: {video_path}",
        flush=True
    )

    # STEP 5
    print("\nSTEP 5: Preparing metadata...", flush=True)

    description = create_description(
        plan,
        wiki,
        credits
    )

    title = str(
        plan.get("title", plan["topic"])
    )[:95]

    tags = [
        str(tag)[:30]
        for tag in plan.get("tags", [])[:15]
    ]

    tags += [
        "Shorts",
        "Hinglish",
        "Facts"
    ]

    tags = list(dict.fromkeys(tags))

    # STEP 6
    print("\nSTEP 6: Uploading to YouTube...", flush=True)

    video_id = upload_video(
        video_path=video_path,
        title=title,
        description=description,
        tags=tags
    )

    if not video_id:
        raise RuntimeError(
            "YouTube upload returned no video ID."
        )

    # STEP 7
    print("\nSTEP 7: Saving topic history...", flush=True)

    history.append({
        "topic": plan["topic"],
        "slug": plan.get("topic_slug", ""),
        "video_id": video_id
    })

    save_history(history)

    print("\n" + "=" * 50)
    print("AUTOFACT AI COMPLETED SUCCESSFULLY")
    print(f"Topic: {plan['topic']}")
    print(f"Video: https://www.youtube.com/shorts/{video_id}")
    print("=" * 50)


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":

    try:
        main()

    except Exception as exc:

        print(
            f"\nAUTOFACT AI FAILED: {exc}",
            file=sys.stderr,
            flush=True
        )

        raise
