# Iso Finder

Take a photo of a plant piping iso. Claude reads the printed coordinates and tells you the floor and the North and East columns where the pipe is, then builds a checklist (pipe, elbows, tees, valves, flanges, gaskets, bolts, tags like J1/J2, supports, welds, cut lengths) to tick off while you walk the line.

## Run it on Streamlit Community Cloud

1. Put these files in a GitHub repository (all of them, keep the folder layout):
   `app.py`, `iso_reader.py`, `iso_finder.html`, `landing.html`, `requirements.txt`, `.streamlit/config.toml`, `.gitignore`
2. Go to https://share.streamlit.io, sign in with GitHub, choose **Create app**, pick the repository, branch `main`, main file `app.py`.
3. Before deploying, open **Advanced settings, Secrets** and paste (see `secrets.toml.example`):

   ```
   ANTHROPIC_API_KEY = "sk-ant-..."
   ACCESS_CODE = "choose-a-code"
   MAX_READS_PER_DAY = 100
   FREE_READS_PER_MONTH = 10
   ```
4. Deploy. The first build takes a couple of minutes.

Never commit your real key. `.gitignore` already skips `.streamlit/secrets.toml`.

## Costs and limits

Each full read makes several Claude calls (about 10 to 15 cents). `FREE_READS_PER_MONTH` caps each signed-in user and `MAX_READS_PER_DAY` caps the whole app, so a bill cannot run away. Set a monthly spend limit in the Anthropic Console as well. `ACCESS_CODE` keeps strangers out.

## Files

- `app.py`: the Streamlit page, upload box, limits and the Claude call.
- `iso_reader.py`: turns the photo into what Claude reads (straighten, sharpen, zoom, find tables) and parses the replies.
- `iso_finder.html`: the interface (floor and column answer, maps, checklist). The reading is passed to it by `app.py`.

Ticks on the checklist are saved in the visitor's browser on that device.

## Accounts, limits and tracking

Sign-in, per-person limits and the Admin page need two free accounts. Do these once.

**Database (Supabase)**
1. supabase.com > New project. Open **SQL Editor**, paste all of `setup.sql`, press Run.
2. Project Settings > API. Copy the Project URL and the `service_role` key into the secrets as `SUPABASE_URL` and `SUPABASE_KEY`.

**Sign-in (Google)**
1. console.cloud.google.com > create a project > APIs & Services > OAuth consent screen (External) > then Credentials > Create OAuth client ID > Web application.
2. Authorized redirect URI: `https://YOUR-APP.streamlit.app/oauth2callback` (use your real app address).
3. Copy the client id and secret into the `[auth]` block in the secrets (see `secrets.toml.example`). Add `cookie_secret` as any long random text.
4. Needs Streamlit 1.42 or newer (already set in requirements).

**Admin**
Put your email in `ADMIN_EMAILS`. Sign in, then use the Admin page link at the top to see reads per person, change a limit, or block someone.

How it works: every read is checked before Claude is called (blocked, monthly limit, app daily limit, 30 second cooldown). A read that fails is not counted. Photos are never saved.
