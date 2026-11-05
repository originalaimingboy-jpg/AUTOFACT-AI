import json
import os
import re
import sys
import time
import random
from pathlib import Path

from google import genai
from google.genai import types

from research import get_wikipedia_context
from video_builder import build_video
from youtube_upload import upload_video


# ============================================================
# CONFIGURATION
# ============================================================

ROOT = Path(__file__).resolve().parent

OUTPUT_DIR = Path(
    os.getenv("OUTPUT_DIR", "work")
)
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

HISTORY_PATH = ROOT / "used_topics.json"
STATE_PATH = ROOT / "autofact_state.json"

# SAME MODEL FOR ALL API KEYS
DEFAULT_MODEL = "gemini-3.8-flash"

MAX_HISTORY = 200

TARGET_SECONDS = int(
    os.getenv("VIDEO_SECONDS", "35")
)
TARGET_SECONDS = max(30, min(40, TARGET_SECONDS))

VOICE_NAME = os.getenv(
    "VOICE_NAME",
    "hi-IN-MadhurNeural"
)


class QuotaPause(Exception):
    pass


# ============================================================
# ENVIRONMENT
# ============================================================

def require_env(name):
    value = os.getenv(name)

    if not value:
        raise RuntimeError(
            f"Missing required environment variable: {name}"
        )

    return value.strip()


# ============================================================
# GEMINI CLIENT SETUP
# ============================================================

def create_gemini_clients():

    primary_key = require_env("GEMINI_API_KEY")

    backup_keys_text = os.getenv(
        "GEMINI_BACKUP_API_KEYS", ""
    ).strip()

    api_keys = [primary_key]

    if backup_keys_text:
        for key in backup_keys_text.split(","):
            key = key.strip()

            if key and key not in api_keys:
                api_keys.append(key)

    clients = []

    for index, api_key in enumerate(api_keys):

        try:
            client = genai.Client(
                api_key=api_key,
                http_options=types.HttpOptions(
                    retry_options=types.HttpRetryOptions(
                        attempts=1,
                        http_status_codes=[]
                    )
                )
            )

            clients.append({
                "client": client,
                "number": index + 1
            })

        except Exception as exc:
            print(
                f"Could not initialize API key {index + 1}: {exc}",
                flush=True
            )

    if not clients:
        raise RuntimeError(
            "No Gemini API clients could be initialized."
        )

    print(
        f"Gemini API clients initialized: {len(clients)}",
        flush=True
    )

    return clients


# ============================================================
# HISTORY MANAGEMENT
# ============================================================

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

    data = {
        "topics": history[-MAX_HISTORY:]
    }

    temporary_path = HISTORY_PATH.with_suffix(".tmp")

    temporary_path.write_text(
        json.dumps(
            data,
            ensure_ascii=False,
            indent=2
        ),
        encoding="utf-8"
    )

    temporary_path.replace(HISTORY_PATH)


# ============================================================
# STATE / RESUME SYSTEM
# ============================================================

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
        json.dumps(
            state,
            ensure_ascii=False,
            indent=2
        ),
        encoding="utf-8"
    )

    temporary_path.replace(STATE_PATH)


def clear_state():

    if STATE_PATH.exists():
        STATE_PATH.unlink()


# ============================================================
# JSON PROCESSING
# ============================================================

def clean_json(text):

    if not text:
        raise RuntimeError(
            "Gemini returned an empty response."
        )

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
# GEMINI ERROR DETECTION
# ============================================================

def is_quota_error(error):

    message = str(error).upper()

    return any(code in message for code in [
        "RESOURCE_EXHAUSTED",
        "429",
        "QUOTA EXCEEDED"
    ])


def is_auth_error(error):

    message = str(error).upper()

    return any(code in message for code in [
        "API_KEY_INVALID",
        "API KEY NOT VALID",
        "INVALID API KEY",
        "UNAUTHENTICATED",
        "PERMISSION_DENIED",
        "401",
        "403"
    ])


def is_temporary_error(error):

    message = str(error).upper()

    return any(code in message for code in [
        "503",
        "502",
        "500",
        "504",
        "408",
        "UNAVAILABLE",
        "INTERNAL",
        "SERVER ERROR",
        "TIMEOUT"
    ])


# ============================================================
# GEMINI REQUEST SYSTEM
# ============================================================

def ask_gemini(clients, model, prompt):

    last_error = None

    for key_data in clients:

        client = key_data["client"]
        key_number = key_data["number"]

        print(
            f"\nUsing Gemini API key {key_number}",
            flush=True
        )

        # Retry temporary errors on the SAME key.
        for attempt in range(3):

            try:

                print(
                    f"Trying model: {model} | Attempt {attempt + 1}/3",
                    flush=True
                )

                response = client.models.generate_content(
                    model=model,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        temperature=0.75,
                        response_mime_type="application/json"
                    )
                )

                if not response.text:
                    raise RuntimeError(
                        "Gemini returned an empty response."
                    )

                result = clean_json(response.text)

                print(
                    f"Gemini success: {model}",
                    flush=True
                )

                return result

            except Exception as exc:

                last_error = exc

                print(
                    f"Gemini request failed: {exc}",
                    flush=True
                )

                # Do not rotate keys to bypass quota.
                if is_quota_error(exc):
                    raise QuotaPause(
                        f"Gemini quota limit reached: {exc}"
                    )

                # Try another configured key only for
                # authentication / key configuration errors.
                if is_auth_error(exc):

                    print(
                        f"API key {key_number} authentication failed.",
                        flush=True
                    )

                    break

                # Retry temporary server errors.
                if is_temporary_error(exc):

                    if attempt < 2:

                        delay = (
                            min(30, 5 * (2 ** attempt))
                            + random.uniform(0, 2)
                        )

                        print(
                            f"Temporary error. Retrying in {delay:.1f} seconds.",
                            flush=True
                        )

                        time.sleep(delay)
                        continue

                    raise RuntimeError(
                        f"Gemini server remained unavailable after retries: {exc}"
                    )

                # Unknown errors should not silently rotate keys.
                raise

    raise RuntimeError(
        f"All configured Gemini API keys failed authentication. "
        f"Last error: {last_error}"
    )


# ============================================================
# STEP 1: CREATE VIDEO PLAN
# ============================================================

def create_plan(
    clients,
    model,
    history,
    target_seconds
):

    recent_topics = [
        item.get("topic", "")
        for item in history[-40:]
    ]

    prompt = f"""
You are AUTOFACT AI.

You are a professional YouTube Shorts production team:

- Creative director
- Researcher
- Hinglish scriptwriter
- Documentary storyteller
- Visual planner

Create ONE highly engaging, accurate and original
YouTube Short.

VIDEO TARGET:

- Duration: approximately {target_seconds} seconds.
- Language: natural Roman-script Hinglish.
- Audience: general Indian audience.
- Voice: conversational male Hindi narration.
- Format: vertical YouTube Shorts.

TOPIC CATEGORIES:

Science, space, geography, history, archaeology,
technology, nature, discoveries and non-graphic mysteries.

Avoid:

Graphic violence, weapon instructions, crime tutorials,
self-harm, medical advice, financial advice, political
persuasion, unverified conspiracy claims and misinformation.

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
10. Remove doubtful factual claims.
11. Avoid unnecessary filler sentences.
12. Make the script suitable for a 35-second Short.

VISUAL RULES:

- Exactly 7 scenes.
- Every scene should have a different visual angle.
- Prefer real photographs and documentary footage.
- Visuals must relate directly to narration.
- Use specific English search queries.
- Avoid generic visual queries.
- Do not present illustrative visuals as authentic evidence.
- Captions should be short, ideally 2–5 words.
- Never put the complete narration in captions.
- Every scene should have a distinct visual subject.

NARRATION:

- Approximately 65–85 words.
- Natural spoken Hinglish.
- Suitable for 30–40 seconds.
- Strong hook in the first sentence.
- No unnecessary pauses or filler.

Return valid JSON only.

SCHEMA:

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

- Exactly seven scenes.
- Narration approximately 65–85 words.
- First three seconds must be interesting.
- Captions must be short.
- Return valid JSON only.
"""

    return ask_gemini(
        clients,
        model,
        prompt
    )


# ============================================================
# STEP 2: FACT CHECK
# ============================================================

def refine_plan(
    clients,
    model,
    plan,
    wiki_context,
    target_seconds
):

    prompt = f"""
You are AUTOFACT AI's strict fact-checking editor.

Improve the supplied video plan.

SOURCE:

Use the supplied Wikipedia context to verify
specific factual claims.

RULES:

1. Remove unsupported claims.
2. Do not invent facts.
3. Preserve the original topic.
4. Keep natural Roman-script Hinglish.
5. Improve awkward sentences.
6. Keep the opening hook interesting.
7. Keep the ending satisfying.
8. Preserve title and description meaning.
9. Keep exactly seven scenes.
10. Keep visuals relevant to narration.
11. Do not add unsupported statistics or dates.
12. Avoid repetitive narration.
13. Keep captions short.
14. Do not put full narration in captions.
15. Keep narration approximately 65–85 words.

TARGET DURATION:

Approximately {target_seconds} seconds.

SOURCE CONTEXT:

{wiki_context[:9000]}

DRAFT PLAN:

{json.dumps(plan, ensure_ascii=False)}

Return the complete corrected JSON using
exactly the same schema.

Return valid JSON only.
"""

    return ask_gemini(
        clients,
        model,
        prompt
    )


# ============================================================
# STEP 3: VALIDATE PLAN
# ============================================================

def validate_plan(plan):

    if not isinstance(plan, dict):
        raise RuntimeError(
            "Invalid plan format."
        )

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
        raise RuntimeError(
            "Scenes must be a list."
        )

    if len(plan["scenes"]) != 7:
        raise RuntimeError(
            f"Expected exactly 7 scenes, got {len(plan['scenes'])}."
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

    if narration_words < 55 or narration_words > 100:
        raise RuntimeError(
            f"Invalid narration length: {narration_words} words"
        )

    print(
        f"Plan validated: {narration_words} words, 7 scenes.",
        flush=True
    )


# ============================================================
# STEP 4: YOUTUBE DESCRIPTION
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

    description = plan.get(
        "description_intro",
        ""
    ).strip()

    if source_line:

        description += (
            "\n\nResearch context: "
            + source_line[4:].strip()
        )

    description += (
        "\n\nVisual credits and licences:\n"
    )

    if credits:

        description += "\n".join(credits)

    else:

        description += (
            "No external media credits were returned."
        )

    description += (
        "\n\nThis video uses illustrative visuals "
        "and AI-generated narration."
    )

    hashtags = plan.get(
        "hashtags",
        ["#Shorts", "#Facts"]
    )

    description += (
        "\n\n"
        + " ".join(
            str(tag)
            for tag in hashtags[:8]
        )
    )

    return description[:4900]


# ============================================================
# MAIN AUTOMATION
# ============================================================

def main():

    print("=" * 55)
    print("AUTOFACT AI STARTING")
    print("=" * 55)

    print(
        f"Target duration: {TARGET_SECONDS} seconds",
        flush=True
    )

    print(
        f"Voice: {VOICE_NAME}",
        flush=True
    )

    state = load_state()

    retry_only = (
        os.getenv(
            "AUTOFACT_RETRY_ONLY",
            "false"
        ).lower() == "true"
    )

    if retry_only and not state:

        print(
            "No pending task. Scheduled retry skipped.",
            flush=True
        )

        return

    model = (
        os.getenv("GEMINI_MODEL")
        or DEFAULT_MODEL
    ).strip()

    print(
        f"Gemini model: {model}",
        flush=True
    )

    clients = create_gemini_clients()

    history = load_history()

    try:

        # ----------------------------------------------------
        # STEP 1: PLAN
        # ----------------------------------------------------

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
                    clients,
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

        # ----------------------------------------------------
        # STEP 2: RESEARCH
        # ----------------------------------------------------

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

        # ----------------------------------------------------
        # STEP 3: FACT CHECK
        # ----------------------------------------------------

        if state.get("stage") != "fact_checked":

            print(
                "\nSTEP 3: Fact-checking script...",
                flush=True
            )

            try:

                plan = refine_plan(
                    clients,
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

        # ----------------------------------------------------
        # STEP 4: BUILD VIDEO
        # ----------------------------------------------------

        print(
            "\nSTEP 4: Building video...",
            flush=True
        )

        video_path, credits = build_video(
            plan,
            OUTPUT_DIR,
            VOICE_NAME,
            TARGET_SECONDS
        )

        video_file = Path(video_path)

        if not video_file.exists():

            raise RuntimeError(
                "Video builder did not create the expected video."
            )

        if video_file.stat().st_size < 100000:

            raise RuntimeError(
                "Generated video file is unexpectedly small."
            )

        print(
            f"Video created: {video_path}",
            flush=True
        )

        # ----------------------------------------------------
        # STEP 5: METADATA
        # ----------------------------------------------------

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
            plan.get(
                "title",
                plan["topic"]
            )
        )[:95]

        tags = [
            str(tag)[:30]
            for tag in plan.get(
                "tags",
                []
            )[:15]
        ]

        tags += [
            "Shorts",
            "Hinglish",
            "Facts"
        ]

        tags = list(dict.fromkeys(tags))

        # ----------------------------------------------------
        # STEP 6: YOUTUBE UPLOAD
        # ----------------------------------------------------

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
            f"YouTube video ID: {video_id}",
            flush=True
        )

        # ----------------------------------------------------
        # STEP 7: SAVE HISTORY
        # ----------------------------------------------------

        print(
            "\nSTEP 7: Saving topic history...",
            flush=True
        )

        history.append({
            "topic": plan["topic"],
            "slug": plan.get(
                "topic_slug",
                ""
            ),
            "video_id": video_id
        })

        save_history(history)

        clear_state()

        print("\n" + "=" * 55)
        print("AUTOFACT AI COMPLETED SUCCESSFULLY")
        print(f"Topic: {plan['topic']}")
        print(
            f"Video: https://www.youtube.com/shorts/{video_id}"
        )
        print("=" * 55)

    except QuotaPause as exc:

        print("\n" + "=" * 55)
        print("GEMINI QUOTA LIMIT REACHED")
        print("Progress saved.")
        print("AUTOFACT AI PAUSED.")
        print("A later scheduled run can retry.")
        print(str(exc))
        print("=" * 55)


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