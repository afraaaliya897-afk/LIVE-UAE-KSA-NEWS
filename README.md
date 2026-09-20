# Construction News App - UAE & Saudi Arabia

Automated construction news aggregator that fetches, classifies, and posts news from 38 trusted sources to WhatsApp - continuously, the moment a new non-duplicate story is confirmed.

## 📁 Project Structure

```
NEWSAPP/
├── app.py                      # Flask web application (main interface)
├── pipeline.py                 # News fetching & classification logic
├── sources.py                  # Source configuration (38 publishers)
├── whatsapp_selfhosted.js      # Self-hosted WhatsApp bot (Node.js)
├── alerter_selfhosted.py       # Manual one-shot fallback (auto-posting lives in app.py)
├── selfhosted_config.json      # WhatsApp group configuration
├── sent_whatsapp.json          # Tracks sent articles (auto-generated)
├── settings.json                # Auto-send on/off toggle (auto-generated)
├── news_log.json                # Discovery history - everything found so far (auto-generated)
├── requirements.txt            # Python dependencies
├── package.json                # Node.js dependencies
├── .gitignore                  # Git ignore rules
├── templates/
│   └── index.html              # Web UI template
├── cache/                      # Search results cache (auto-generated)
└── .whatsapp-session-new/      # WhatsApp session data (auto-generated)
```

## 🚀 Quick Start

### 1. Install Dependencies

**Python:**
```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

**Node.js:**
```bash
npm install
npx puppeteer browsers install chrome
```

### 2. Configure WhatsApp

Update `selfhosted_config.json` with your group ID:
```json
{
  "groupId": "YOUR_GROUP_ID@g.us",
  "botApiUrl": "http://localhost:3000"
}
```

### 3. Start Services

**Terminal 1 - WhatsApp Bot:**
```bash
node whatsapp_selfhosted.js
# Scan QR code with spare WhatsApp number
```

**Terminal 2 - Web App:**
```bash
python app.py
# Open http://127.0.0.1:5050
```

## 📊 Features

### Web Interface (http://127.0.0.1:5050)
- ✅ Search 38 sources in parallel (~15 seconds)
- ✅ Smart deduplication (same news only once, even across different publishers)
- ✅ Date range filtering
- ✅ Category filtering (Contract/Project Awarded)
- ✅ Live search within results
- ✅ Sort by date, source, category
- ✅ Export to CSV
- ✅ **One-click auto-send toggle** - on posts automatically, off means nothing sends until you do it manually
- ✅ **News Log** - everything discovered so far, sent or not
- ✅ Dark mode
- ✅ Mobile responsive

### News Classification
- **Categories**: Contract Awarded, Project Awarded
- **Countries**: UAE, Saudi Arabia
- **Industries**: Construction, infrastructure, development
- **Accuracy**: ~89% with 25-40 results per search

### WhatsApp Integration
- ✅ Self-hosted (no third-party access)
- ✅ Duplicate prevention
- ✅ Posts as soon as a new story is confirmed; ~15-20s gap between messages when several land at once (spam protection)
- ✅ Formatted messages with category, country, title, source, link
- ✅ Tracks sent articles to avoid re-posting

## 📚 Core Files Explained

### `app.py` - Web Application
- Flask web server
- Handles search requests, renders results
- A background poller checks for fresh news continuously (every 10 minutes) and a background sender posts anything new the instant it's confirmed unique, whenever the auto-send toggle is on
- WhatsApp integration endpoints

### `pipeline.py` - Core Logic
- Fetches news from Google News RSS
- Parallel async requests
- Classifies by category and country
- Caches results (30 minutes, manual Search only)
- Shared cross-source deduplication logic, used by both Search and the Live poller

### `sources.py` - Configuration
- 38 trusted publishers
- Award keywords (wins, secured, awarded, etc.)
- Construction keywords (building, infrastructure, etc.)
- Country keywords (Dubai, Riyadh, NEOM, etc.)

### `whatsapp_selfhosted.js` - WhatsApp Bot
- Express.js API server (port 3000)
- WhatsApp Web automation via Puppeteer
- Endpoints: `/status`, `/groups`, `/send`
- Session management

### `alerter_selfhosted.py` - Manual Fallback (Optional)
- One-shot check you can run by hand (`python alerter_selfhosted.py [--dry-run]`)
- `app.py` is now the continuous auto-poster, so `--watch` here is deprecated - running it alongside `app.py` would double-post

## 🎯 Daily Usage

1. Open http://127.0.0.1:5050 and start `node whatsapp_selfhosted.js` in another terminal
2. Go to the **Live** tab and flip **Auto-send** on - fresh, non-duplicate news then posts to WhatsApp automatically, paced ~15-20s apart when several stories land at once
3. Use the **News Log** tab to see everything that's been discovered so far, and the **Send Log** tab to see what actually went to WhatsApp
4. Use **Search** any time for a manual lookup, and "Queue for WhatsApp" / "Post this" to send something by hand regardless of the toggle

### One-shot manual check (optional)
```bash
python alerter_selfhosted.py --dry-run
# Prints what it would send, without posting
```

## 🔧 Configuration

### `selfhosted_config.json`
```json
{
  "groupId": "YOUR_GROUP_ID@g.us",
  "botApiUrl": "http://localhost:3000"   # Local bot API
}
```

### `.gitignore`
Ensures sensitive data is not committed:
- `.whatsapp-session*/` - WhatsApp session data
- `sent_whatsapp.json` - Sent articles tracking
- `selfhosted_config.json` - WhatsApp configuration
- `cache/` - Search cache
- `.venv/` - Python virtual environment

## 🌐 Sources (38)

### Major Construction Publications
- Zawya, MEED, Construction Week Online

### UAE News
- Khaleej Times, Gulf News, The National, Emirates 24/7

### Saudi Arabia News
- Arab News, Saudi Gazette, Okaz, Al Riyadh

### Business & Trade
- Arabian Business, Gulf Business, Trade Arabia

### Official Agencies
- WAM (UAE), SPA (Saudi Arabia)

[See `sources.py` for complete list]

## 🛡️ Safety Features

### WhatsApp Bot Safety
- ✅ Self-hosted (no third-party access)
- ✅ Uses spare SIM (protects main account)
- ✅ ~15-20s pacing between consecutive auto-sends (spam protection)
- ✅ Duplicate prevention
- ✅ One toggle to stop all auto-posting instantly if needed

### Data Privacy
- ✅ All data stored locally
- ✅ No external API calls (except Google News RSS)
- ✅ Session data encrypted by WhatsApp

## 📝 Notes

### Google News RSS Limitations
- Results typically from last 30 days
- Historical searches beyond 30 days have limited coverage
- Best results: Leave dates empty for today's news

### Deduplication
- Same news from different sources appears only once
- Keeps version from the most trusted source (order in `sources.py`)
- 85% word similarity threshold
- Runs identically for manual Search and the automatic Live poller (shared logic in `pipeline.py`)

### Performance
- First search: ~15-20 seconds (76 parallel requests)
- Cached searches: Instant (30-minute cache)
- Live poller: checks for fresh news every 10 minutes
- WhatsApp posting: ~15-20 seconds between consecutive auto-sends

## 🐛 Troubleshooting

### WhatsApp Bot Not Connecting
```bash
# Reinstall Chrome for Puppeteer
npx puppeteer browsers install chrome

# Restart bot
node whatsapp_selfhosted.js
```

### Port Already in Use
```bash
# Kill processes
taskkill /F /IM node.exe
taskkill /F /IM python.exe
```

### Cache Issues
```bash
# Clear cache for fresh results
Remove-Item cache\*.json
```

## 📦 Dependencies

### Python
- flask - Web framework
- feedparser - RSS parsing
- aiohttp - Async HTTP requests
- requests - HTTP client

### Node.js
- whatsapp-web.js - WhatsApp automation
- puppeteer - Browser automation
- express - API server
- qrcode-terminal - QR code display

## 🎓 Understanding the Workflow

Automatic (continuous, once auto-send is on):
```
1. BACKGROUND POLLER (app.py, every 10 min)
   ↓
2. PIPELINE (pipeline.py)
   ├─ Fetch from 38 sources (parallel)
   ├─ Classify articles
   ├─ Deduplicate (same story across publishers -> one entry)
   └─ Return results
   ↓
3. MERGE into today's live feed + NEWS LOG (everything discovered)
   ↓
4. Genuinely new items → SEND QUEUE
   ↓
5. BACKGROUND SENDER (app.py, ticks every 5s)
   ├─ Only sends if the auto-send toggle is on
   ├─ Paces consecutive sends ~15-20s apart
   └─ WhatsApp Bot (whatsapp_selfhosted.js) posts to the group, tracks sent articles
```

Manual (any time, toggle or no toggle):
```
1. USER SEARCHES or clicks "Post this" on a Live-tab item
   ↓
2. FLASK (app.py) → WhatsApp Bot API → posted immediately
```

## 📄 License

Private project for internal use.

---

**Made with ❤️ for construction news aggregation**
