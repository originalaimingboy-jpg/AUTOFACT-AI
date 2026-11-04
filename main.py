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
STATE_PATH = ROOT / "autofact_state.json"

DEFAULT_MODEL = "gemini-3.8-flash"
FALLBACK_MODEL = os.getenv("GEMINI_FALLBACK_MODEL", "").strip()

MAX_HISTORY = 200
TARGET_SECONDS = int(os.getenv("VIDEO_SECONDS", "52"))
VOICE_NAME = os.getenv("VOICE_NAME", "hi-IN-MadhurNeural")


class QuotaPause(Exception):
    pass


def require_env(name):
    value = os.getenv(name)

    if not value:
        raise RuntimeError(
            f"Missing required environment variable: {name}"
        )

    return value


def load_history():
    try:
        data = json.loads(
            HISTORY_PATH.read_text(encoding="utf-8")
        )
        return data.get("topics", [])

    except FileNotFoundError:
        return []

    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"History file is invalid: {exc}"
        )


def save_history(history):
    data = {"topics": history[-MAX_HISTORY:]}

    HISTORY_PATH.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )


def load_state():
    if not STATE_PATH.exists():
        return {}

    try:
        return json.loads(
            STATE_PATH.read_text(encoding="utf-8")
        )

    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"Progress state file is invalid: {exc}"
        )


def save_state(state):
    temporary_path = STATE_PATH.with_suffix(".tmp")

    temporary_path.write_text(
        json.dumps(state, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )

    temporary_path.replace(STATE_PATH)


def clear_state():
    if STATE_PATH.exists():
        STATE_PATH.unlink()


def clean_json(text):
    if not text:
        raise RuntimeError("Gemini returned an empty response.")

    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)

    return json.loads(text)


def is_quota_error(error):
    message = str(error).upper()

    return (
        "RESOURCE_EXHAUSTED" in message
        or "429" in message
        or "QUOTA EXCEEDED" in message
    )


def ask_gemini(client, model, prompt):
    models_to_try = [model]

    if FALLBACK_MODEL and FALLBACK_MODEL != model:
        models_to_try.append(FALLBACK_MODEL)

    last_error = None
    quota_seen = False

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

            print(
                f"Model failed: {current_model}",
                flush=True
            )
            print(f"Reason: {exc}", flush=True)

            if is_quota_error(exc):
                quota_seen = True
                continue

            temporary_errors = [
                "503",
                "502",
                "500",
                "504",
                "UNAVAILABLE",
                "INTERNAL"
            ]

            if not any(
                code in str(exc).upper()
                for code in temporary_errors
            ):
                raise

    if quota_seen:
        raise QuotaPause(str(last_error))

    raise RuntimeError(
        f"All configured Gemini models failed: {last_error}"
    )


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

Create ONE highly engaging, accurate, original YouTube Short.

TARGET:
- Duration: approximately {target_seconds} seconds
- Language: natural Roman-script Hinglish
- Audience: general Indian audience
- Voice: conversational male Hindi narration

TOPIC CATEGORIES:
Science, space, geography, history, archaeology,
technology, nature, discoveries and non-graphic mysteries.

Avoid:
Graphic violence, weapon instructions, crime tutorials,
self-harm, medical advice, financial advice, political persuasion,
unverified conspiracy claims and sensational misinformation.

PREVIOUS TOPICS:
{json.dumps(recent_topics, ensure_ascii=False)}

Never repeat an already covered topic.

STORYTELLING RULES:
1. Start with a powerful curiosity-based hook.
2. No greetings or introductions.
3. Every sentence should reveal something interesting.
4. Use simple conversational Hinglish.
5. Avoid robotic or textbook-style narration.
6. Build curiosity progressively.
7. Use short natural spoken sentences.
8. End with a memorable takeaway.
9. Never invent statistics, dates, quotations or events.
10. Internally review factual claims and remove doubtful details.

VISUAL RULES:
- Exactly 5 scenes.
- Different visual subject or angle in every scene.
- Cinematic documentary-style descriptions.
- Visuals must relate to narration.
- Do not present illustrative visuals as authentic historical evidence.
- Captions must be short and readable.

Return valid JSON only:

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
- Narration approximately 110-135 words.
- First 3 seconds must be interesting.
- Do not put entire narration into captions.
- Return valid JSON only.
"""

    return ask_gemini(client, model, prompt)


def refine_plan(client, model, plan, wiki_context, target_seconds):
    prompt = f"""
You are AUTOFACT AI's strict fact-checking editor.

Improve the supplied video plan.

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
8. Preserve title and description meaning.
9. Keep exactly five scenes.
10. Keep visuals relevant to narration.
11. Do not add unsupported statistics or dates.
12. Avoid repetitive narration.

TARGET DURATION:
Approximately {target_seconds} seconds.

SOURCE CONTEXT:
{wiki_context[:9000]}

DRAFT PLAN:
{json.dumps(plan, ensure_ascii=False)}

Return the complete corrected JSON using exactly the same schema.
"""

    return ask_gemini(client, model, prompt)


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
            "Expected exactly five scenes."
        )

    for index, scene in enumerate(plan["scenes"]):
        if not isinstance(scene, dict):
            raise RuntimeError(
                f"Invalid scene {index + 1}"
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

    if narration_words < 60 or narration_words > 160:
        raise RuntimeError(
            f"Invalid narration length: {narration_words} words"
        )

    print(
        f"Plan validated: {narration_words} words, 5 scenes.",
        flush=True
    )


def create_description(plan, wiki, credits):
    source_line = next(
        (
            line
            for line in wiki.splitlines()
            if line.startswith("URL:")
        ),
        ""
    )

    description = plan.get(
        "description_intro", ""
    ).strip()

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


def main():
    print("=" * 50)
    print("AUTOFACT AI STARTING")
    print("=" * 50)

    state = load_state()

    retry_only = (
        os.getenv("AUTOFACT_RETRY_ONLY", "false").lower()
        == "true"
    )

    if retry_only and not state:
        print(
            "No pending task. Scheduled retry skipped.",
            flush=True
        )
        return

    api_key = require_env("GEMINI_API_KEY")

    model = (
        os.getenv("GEMINI_MODEL")
        or DEFAULT_MODEL
    )

    print(f"Primary model: {model}", flush=True)

    if FALLBACK_MODEL:
        print(
            f"Backup model: {FALLBACK_MODEL}",
            flush=True
        )

    retry_options = types.HttpRetryOptions(
        attempts=3,
        initial_delay=3,
        max_delay=30,
        exp_base=2,
        jitter=1,
        http_status_codes=[
            408,
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

    try:
        if state.get("plan"):
            plan = state["plan"]
            print(
                "Resuming saved video plan.",
                flush=True
            )
        else:
            print(
                "\nSTEP 1: Creating video plan...",
                flush=True
            )

            try:
                plan = create_plan(
                    client,
                    model,
                    history,
                    TARGET_SECONDS
                )
            except QuotaPause:
                save_state({
                    "stage": "need_plan"
                })
                raise

            validate_plan(plan)

            state = {
                "stage": "plan_created",
                "plan": plan
            }
            save_state(state)

        validate_plan(plan)

        if state.get("wiki"):
            wiki = state["wiki"]
            print(
                "Resuming saved research context.",
                flush=True
            )
        else:
            print(
                "\nSTEP 2: Researching topic...",
                flush=True
            )

            wiki = get_wikipedia_context(
                plan.get(
                    "wikipedia_search",
                    plan["topic"]
                )
            )

            if not wiki:
                save_state({
                    "stage": "plan_created",
                    "plan": plan
                })
                raise RuntimeError(
                    "Wikipedia research failed. "
                    "Saved plan for the next run."
                )

            state = {
                "stage": "researched",
                "plan": plan,
                "wiki": wiki
            }
            save_state(state)

        if state.get("stage") != "fact_checked":
            print(
                "\nSTEP 3: Fact-checking script...",
                flush=True
            )

            try:
                plan = refine_plan(
                    client,
                    model,
                    plan,
                    wiki,
                    TARGET_SECONDS
                )
            except QuotaPause:
                save_state({
                    "stage": "researched",
                    "plan": plan,
                    "wiki": wiki
                })
                raise

            validate_plan(plan)

            state = {
                "stage": "fact_checked",
                "plan": plan,
                "wiki": wiki
            }
            save_state(state)

        else:
            plan = state["plan"]
            wiki = state["wiki"]

        print(
            "\nSTEP 4: Building video...",
            flush=True
        )

        video_path, credits = build_video(
            plan,
            OUTPUT_DIR,
            VOICE_NAME
        )

        if not Path(video_path).exists():
            raise RuntimeError(
                "Video builder did not create the expected video."
            )

        print(
            f"Video created: {video_path}",
            flush=True
        )

        print(
            "\nSTEP 5: Preparing metadata...",
            flush=True
        )

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

        print(
            "\nSTEP 6: Uploading to YouTube...",
            flush=True
        )

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

        print(
            "\nSTEP 7: Saving topic history...",
            flush=True
        )

        history.append({
            "topic": plan["topic"],
            "slug": plan.get("topic_slug", ""),
            "video_id": video_id
        })

        save_history(history)
        clear_state()

        print("\n" + "=" * 50)
        print("AUTOFACT AI COMPLETED SUCCESSFULLY")
        print(f"Topic: {plan['topic']}")
        print(
            f"Video: https://www.youtube.com/shorts/{video_id}"
        )
        print("=" * 50)

    except QuotaPause as exc:
        print("\n" + "=" * 50)
        print("GEMINI QUOTA LIMIT REACHED")
        print("Progress saved successfully.")
        print("AUTOFACT AI PAUSED.")
        print("GitHub Actions will retry automatically.")
        print(str(exc))
        print("=" * 50)


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