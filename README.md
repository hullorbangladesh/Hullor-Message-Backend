# Facebook Shop Bot (Django + Gemini)

Customer Messenger e message dile Gemini (free) reply dibe: product khuje dekhabe, price bolbe, order nibe (COD, Dhaka ৳80 / bahire ৳130), ar order Admin Panel e dekhabe. Bot jaa pare na, oita human er kache hand over korbe.

```
Customer → Messenger → Meta webhook → Django (Render) → Gemini
                                         │  ├─ products khuje (Neon Postgres)
                                         │  └─ order save kore
                       Admin Panel (Vercel, HTML) ← Django API
UptimeRobot → /health (5 min por por, server jate ghumiye na jay)
```

**Secret (key/token) kokhono GitHub e ba chat e dibe na.** Shob kichu shudhu Render er Environment Variables e jabe.

---

## Step 1: Database (Neon, free)

1. neon.tech e sign up → **Create project**.
2. Dashboard e **Connection string** copy koro (`postgresql://...?sslmode=require`). "Connection pooling" er toggle thakle off kore direct string nao.
3. Eta tomar `DATABASE_URL`.

## Step 2: GitHub e upload

GitHub e ekta **Private** repository banao, tarpor ei folder e terminal khule:

```
git init
git add .
git commit -m "first commit"
git branch -M main
git remote add origin https://github.com/TOMAR_NAAM/shop-bot.git
git push -u origin main
```
(`.env` file `.gitignore` e ache, upload hobe na.)

## Step 3: Render e deploy

1. render.com → **New → Blueprint** → GitHub repo select koro. `render.yaml` poRe nijei service banabe. (Blueprint na chaile **New → Web Service** e: Build `bash build.sh`, Start `gunicorn config.wsgi:application --workers 1 --threads 8 --timeout 60 --bind 0.0.0.0:$PORT`, Health check path `/health`.)
2. Environment variables boshao:

| Name | Value |
|---|---|
| `DATABASE_URL` | Neon er string |
| `GEMINI_API_KEY` | AI Studio er **notun** key |
| `META_APP_SECRET` | Meta App → Settings → Basic |
| `PAGE_ACCESS_TOKEN` | Messenger settings theke **notun** token |
| `VERIFY_TOKEN` | tomar banano random string (Meta te same ta dibe) |
| `ADMIN_API_TOKEN` | kom-pokkhe 30 character random (password generator use koro) |
| `DJANGO_SUPERUSER_USERNAME` / `DJANGO_SUPERUSER_PASSWORD` | Django admin (`/admin`) login |
| `SHOP_NAME` | tomar shop er naam |

   `DJANGO_SECRET_KEY` Render nijei banay. Optional: `GEMINI_MODEL` (default `gemini-flash-lite-latest`), `DELIVERY_CHARGE_DHAKA`, `DELIVERY_CHARGE_OUTSIDE`.
3. Deploy sesh hole `https://TOMAR-APP.onrender.com/health` e gele `ok` dekhbe.

## Step 4: UptimeRobot

uptimerobot.com → **Add New Monitor** → type **HTTP(s)** → URL `https://TOMAR-APP.onrender.com/health` → interval **5 minutes**. `/health` database touch kore na, tai khub halka.

## Step 5: Meta webhook

1. developers.facebook.com → tomar App → **Messenger API settings** (ba Use cases → Messenger → Settings) → **Configure webhooks**.
2. **Callback URL**: `https://TOMAR-APP.onrender.com/webhook`, **Verify token**: tomar `VERIFY_TOKEN` → **Verify and save**.
3. Subscribe fields: **messages** ar **messaging_postbacks**. Tomar Page ta o webhook e subscribed ache kina dekhe nao.
4. Tomar nijer Facebook account theke Page e message pathao. Reply ashle shob thik.

## Step 6: Product add

`https://TOMAR-APP.onrender.com/admin` e login → **Products**.
- Ekta ekta kore add korte paro, **ba** upore **Import CSV** diye shob ekshathe. Format dekho `products_sample.csv` e (Excel/Google Sheets theke "CSV UTF-8" save koro).
- Required: `name`, `price`. Optional: `sku`, `name_bn`, `category`, `keywords`, `description`, `stock`, `image_url`. Same `sku` dile update hoy.
- **`keywords` column e Bangla ar Banglish shobdo dao** (jemon `panjabi punjabi পাঞ্জাবি`), karon customer ra 3 bhashay lekhe ar bot oi shobdo diye khuje.
- Price/stock pore Admin Panel theke-o bodlate parbe.

## Step 7: Shop er policy bot ke shekhao

`bot/shop_info.txt` e delivery time, return policy, warranty, support er shomoy lekho, tarpor GitHub e push koro (Render nijei redeploy korbe). Ei file e ja nei, bot ta guess korbe na, human er kache dibe.

## Step 8: Admin Panel (Vercel)

Admin Panel ei repo te nei, eta alada zip (`Hullor-Admin-Panel.zip`) ar alada GitHub repo. Oi zip er README te deploy er steps ache. Deploy hole:

1. Panel open kore **Backend URL** (`https://TOMAR-APP.onrender.com`) ar `ADMIN_API_TOKEN` dao.
2. Browser notification allow koro. Notun order ashle sound + notification + title e count dekhabe (tab khola thakle).
3. Optional security: Render e `CORS_ALLOWED_ORIGINS=https://TOMAR-PANEL.vercel.app` dao.

Panel e: **Orders** (status bodlano: new → confirmed → shipped → delivered / cancelled; cancel korle stock ferot ashe), **Chats** (bot ja hand over korese, "Resume bot" chaple abar bot chalu), **Products** (price/stock/active).

## Step 9: Test

- Bangla, Banglish, English, 3 vabei product jiggesh koro.
- Pura order flow: product → naam/phone/address/Dhaka na bahire → summary → "confirm" → Orders tab e order.
- "agent er sathe kotha bolbo" bolo → Chats tab e dekhabe, bot chup thakbe. Page inbox theke reply kore **Resume bot** chap.

## Step 10: Shobar jonno live kora

Meta App **Development mode** e thakle shudhu Page er admin/tester/developer ra bot er reply pabe. Public customer der jonno App ke **Live** korte hobe ar `pages_messaging` permission er **Advanced Access** (App Review, kokhono Business Verification o) lagbe. Eta Meta approve korte kichu din lagte pare, tai age theke shuru koro.

---

## Jaante hobe

- **Gemini free tier** er limit ache (per minute / per day). Beshi customer ashle limit hit korbe, tokhon customer ke "ektu pore message din" reply jabe. Free tier e data Google er product unnoyone use hote pare, ar customer er phone/address chat er moddhe thake. Chaile pore paid tier e jete paro (code bodlate hobe na).
- **Render free** e deploy/restart er shomoy kichu second down thake. Meta tokhon abar pathay, ar duplicate reply ota-o atkay.
- **Neon** idle thakle ghumay, prothom message e 1-2 second beshi lagte pare.
- **24 ghonta niyom**: customer last message er 24 ghonta pore bot notun message pathate parbe na.
- Customer **chobi** pathale bot ekhon bujhe na, text e lekhte bole. (Pore add kora jabe.)
- `--workers 1` rekhe dao. Per-customer lock ar spam limit memory te thake.

## Somossha hole

| Lakkhon | Karon / Somadhan |
|---|---|
| Meta webhook verify hocche na | `VERIFY_TOKEN` duto jaygay hubohu same kina dekho; Render e `/health` open hocche kina dekho |
| Bot reply dey na | Render → **Logs** dekho. `Messenger API error` = token/permission, `Gemini error 429` = quota, `404 model` = Render e `GEMINI_MODEL=gemini-flash-latest` dao |
| Shudhu tomar message e reply ashe | App ekhono Development mode e (Step 10) |
| Panel e "unauthorized" | `ADMIN_API_TOKEN` mile na; `503` hole token 20 character er kom |
| Django admin login hoy na | `DJANGO_SUPERUSER_*` dewar por abar deploy koro |

## Local e chalate chaile (optional)

```
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # .env e value boshao
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
python manage.py test       # shob test chalabe
```
