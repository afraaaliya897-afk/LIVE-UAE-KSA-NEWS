# Construction News App - UAE & Saudi Arabia

Automated construction news aggregator that fetches, classifies, and posts news from 70+ trusted sources to WhatsApp.

## 📁 Project Structure

```
NEWSAPP/
├── app.py                      # Flask web application (main interface)
├── pipeline.py                 # News fetching & classification logic
├── sources.py                  # Source configuration (70+ publishers)
├── whatsapp_selfhosted.js      # Self-hosted WhatsApp bot (Node.js)
├── alerter_selfhosted.py       # WhatsApp alerter (optional auto-posting)
├── selfhosted_config.json      # WhatsApp group configuration
├── sent_whatsapp.json          # Tracks sent articles (auto-generated)
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
- ✅ Search 70+ sources in parallel (~15 seconds)
- ✅ Smart deduplication (same news only once)
- ✅ Date range filtering
- ✅ Category filtering (Contract/Project Awarded)
- ✅ Live search within results
- ✅ Sort by date, source, category
- ✅ Export to CSV
- ✅ **Send to WhatsApp button**
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
- ✅ 3-second delay between messages (spam protection)
- ✅ Formatted messages with category, country, title, source, link
- ✅ Tracks sent articles to avoid re-posting

## 📚 Core Files Explained

### `app.py` - Web Application
- Flask web server
- Handles search requests
- Renders results
- WhatsApp integration endpoint

### `pipeline.py` - Core Logic
- Fetches news from Google News RSS
- Parallel async requests (140 searches simultaneously)
- Classifies by category and country
- Caches results (30 minutes)
- Deduplication logic

### `sources.py` - Configuration
- 70+ trusted publishers
- Award keywords (wins, secured, awarded, etc.)
- Construction keywords (building, infrastructure, etc.)
- Country keywords (Dubai, Riyadh, NEOM, etc.)

### `whatsapp_selfhosted.js` - WhatsApp Bot
- Express.js API server (port 3000)
- WhatsApp Web automation via Puppeteer
- Endpoints: `/status`, `/groups`, `/send`
- Session management

### `alerter_selfhosted.py` - Auto-Poster (Optional)
- Automatically checks for new news every 15 minutes
- Posts to WhatsApp group
- Can be used for fully automated workflow

## 🎯 Daily Usage

### Manual Workflow (Recommended)
1. Open http://127.0.0.1:5050
2. Click "Search" (leave dates empty for today's news)
3. Review ~25-40 results
4. Click green "Send to WhatsApp" button
5. News posted to your group!

### Automatic Workflow (Optional)
```bash
python alerter_selfhosted.py --watch
# Checks every 15 minutes, posts new news automatically
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

## 🌐 Sources (70+)

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
- ✅ 3-second delay between messages
- ✅ Duplicate prevention
- ✅ Volume limits (25-40 news per day)

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
- Keeps version from most trusted source
- 70% word similarity threshold

### Performance
- First search: ~15-20 seconds (140 parallel requests)
- Cached searches: Instant (30-minute cache)
- WhatsApp posting: 3 seconds per article

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

```
1. USER SEARCHES
   ↓
2. FLASK (app.py)
   ↓
3. PIPELINE (pipeline.py)
   ├─ Fetch from 70 sources (parallel)
   ├─ Classify articles
   ├─ Deduplicate
   └─ Return results
   ↓
4. USER REVIEWS
   ↓
5. CLICK "SEND TO WHATSAPP"
   ↓
6. FLASK → WhatsApp Bot API
   ↓
7. WhatsApp Bot (whatsapp_selfhosted.js)
   ├─ Format messages
   ├─ Post to group
   └─ Track sent articles
```

## 📄 License

Private project for internal use.

---

**Made with ❤️ for construction news aggregation**
