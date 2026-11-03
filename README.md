# AUTOFACT AI — Autonomous Hinglish YouTube Shorts

A weekly GitHub Actions workflow that:
1. asks Gemini to choose an evergreen, fact-based mystery/science/history topic,
2. checks a Wikipedia article for source context,
3. writes an original 45–60 second Hinglish script and scene plan,
4. finds openly licensed images from Wikimedia Commons,
5. generates a male Hindi voiceover with Edge TTS,
6. assembles a vertical MP4 using FFmpeg,
7. uploads it to YouTube using the YouTube Data API,
8. records the topic so it is not reused.

## Important limitations

- This is a best-effort free-tier setup, not a guaranteed unlimited service.
- Gemini free-tier quotas, GitHub availability, Wikipedia/Commons availability, and Edge TTS can change or fail.
- Edge TTS uses Microsoft's online speech service through an unofficial client library; it is not a guaranteed SLA/API.
- The visuals are animated still images, not AI-generated cinematic video footage.
- A first-time setup is required: create API credentials, authorize YouTube, and add GitHub repository secrets.
- Do not put API keys, OAuth tokens, or passwords in source files or commit them to GitHub.
- YouTube API uploads are public because the workflow explicitly sets `privacyStatus=public`. Test manually first if you do not want immediate public publishing.
- Do not use this system to mass-produce repetitive or misleading videos. Review YouTube policies and asset licenses.

## 1. Create the GitHub repository

1. Sign in to GitHub and create a **public** repository named `AUTOFACT-AI`.
2. Extract this ZIP on your phone.
3. Upload the extracted files and folders to the repository's default branch (`main`).
4. Ensure `.github/workflows/weekly.yml` is in the repository at that exact path.

GitHub Actions standard runners are free for public repositories. Scheduled workflows can be delayed or disabled after 60 days without repository activity. This project commits the topic-history file after a successful run to keep the repository active, but do not rely on schedules as a guaranteed clock.

## 2. Get a Gemini API key

1. Open https://aistudio.google.com/app/apikey
2. Create an API key.
3. In GitHub repository → Settings → Secrets and variables → Actions → New repository secret:
   - Name: `GEMINI_API_KEY`
   - Value: your key

Optional repository variable:
- `GEMINI_MODEL` = `gemini-3.8-flash`

If that model is unavailable to your account, change the variable to a currently available Flash model shown in Google's Gemini API model list:
https://ai.google.dev/gemini-api/docs/models

## 3. Set up YouTube Data API and OAuth

YouTube uploads require OAuth user authorization. An API key alone cannot upload to your channel.

1. Open https://console.cloud.google.com/
2. Create a Google Cloud project.
3. Enable **YouTube Data API v3**.
4. Configure the OAuth consent screen for an external app.
5. Create an OAuth client ID. For the OAuth Playground method below, choose **Web application** and add this authorized redirect URI exactly:
   `https://developers.google.com/oauthplayground`
6. Open https://developers.google.com/oauthplayground
7. Open its settings (gear icon), enable **Use your own OAuth credentials**, and enter the OAuth client ID and client secret.
8. In Step 1, enter this scope:
   `https://www.googleapis.com/auth/youtube.upload`
9. Authorize the account that owns the YouTube channel.
10. In Step 2, exchange the authorization code for tokens. Copy the **refresh_token** from the token response.

For persistent unattended use, do not leave the OAuth consent screen in **Testing**: Google says external apps in Testing generally receive refresh tokens that expire after 7 days for scopes like YouTube upload. Configure the consent screen appropriately for your own use and follow any Google verification requirements. Never share the refresh token with anyone.

Add these GitHub repository secrets:

| Secret name | Value |
|---|---|
| `YT_CLIENT_ID` | OAuth client ID |
| `YT_CLIENT_SECRET` | OAuth client secret |
| `YT_REFRESH_TOKEN` | Refresh token from OAuth Playground |

Do not paste any of these credentials into ChatGPT, public issues, or source files.

## 4. Add the secrets

Repository → Settings → Secrets and variables → Actions → New repository secret.

Required secrets:
- `GEMINI_API_KEY`
- `YT_CLIENT_ID`
- `YT_CLIENT_SECRET`
- `YT_REFRESH_TOKEN`

No Pexels key, payment method, or phone-side app is needed.

## 5. Enable Actions and test

1. Open repository → Actions.
2. If GitHub asks, enable workflows.
3. Select **AUTOFACT AI — Weekly Shorts**.
4. Tap **Run workflow** to test once.
5. Open the run and inspect its logs.
6. On success, the workflow uploads one public Short and prints its YouTube URL.

The weekly schedule is Sunday at 09:00 India time (03:30 UTC). GitHub may run scheduled jobs late.

## 6. How to change settings

Edit `.github/workflows/weekly.yml`:
- `30 3 * * 0` = Sunday 09:00 IST
- `30 3 * * 1` = Monday 09:00 IST
- `30 3 1 * *` = first day of each month, 09:00 IST

Edit the environment values in the workflow to change:
- `VIDEO_LANGUAGE`
- `VIDEO_SECONDS`
- `VOICE_NAME`
- `GEMINI_MODEL`

## What it creates

- 1080 × 1920 vertical MP4
- Hinglish narration
- Animated still-image scenes
- On-screen scene captions
- Original title, description and hashtags
- Asset credits and source links in the description

## Troubleshooting

- **Gemini model not found:** update `GEMINI_MODEL` to a model available in your AI Studio account.
- **Gemini quota exceeded:** wait for quota reset or reduce runs. Do not add billing unless you intentionally want paid usage.
- **No Commons images:** the script uses generated gradient backgrounds as a fallback; it still adds a note in the description.
- **YouTube invalid_grant:** refresh token expired/revoked. Re-authorize and update `YT_REFRESH_TOKEN`. Check OAuth consent-screen publishing status.
- **YouTube quota/auth error:** check YouTube Data API v3 is enabled and that OAuth was authorized with the `youtube.upload` scope.
- **Edge TTS failure:** the external speech endpoint may be unavailable. Retry the workflow later.
- **GitHub schedule did not run:** check Actions is enabled, the workflow is on the default branch, and the repository has not been inactive for 60 days.

## Safety and quality

The script prompt restricts topics to general-audience science, history, geography and mysteries; it avoids dangerous how-to content, medical/financial claims, fabricated facts and graphic material. AI output can still be wrong. This project is an automation starter, not a fact-checking guarantee.
