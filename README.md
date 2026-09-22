# Construction News Monitor - UAE & Saudi Arabia

**Production-ready** news monitoring system for **Project Awarded** and **Contract Awarded** construction news from UAE and Saudi Arabia. Features strict LLM filtering, automated WhatsApp delivery, and comprehensive monitoring UI.

---

## 📁 Project Structure

```
NEWSAPP/
├── 📄 Core Application
│   ├── app.py                      # Flask web server & background automation
│   ├── pipeline.py                 # News fetching & keyword filtering
│   ├── llm_judge.py                # LLM-based strict filtering
│   ├── store.py                    # Data persistence layer
│   └── sources.py                  # 38 trusted publishers + keywords
│
├── 📱 WhatsApp Integration
│   ├── whatsapp_selfhosted.js      # Self-hosted bot (Node.js + Puppeteer)
│   └── selfhosted_config.json      # Group configuration (not in git)
│
├── ⚙️ Configuration
│   ├── requirements.txt            # Python dependencies
│   ├── package.json                # Node.js dependencies
│   ├── .env                        # API keys (not in git)
│   └── .gitignore                  # Git ignore rules
│
├── 🖥️ User Interface
│   └── templates/index.html        # Complete monitoring dashboard
│
└── 📊 Generated Data (not in git)
    ├── extracted_news.json         # After keyword filter
    ├── llm_picks.json              # LLM evaluation results
    ├── live_news.json              # Today's approved news
    ├── send_queue.json             # WhatsApp queue
    ├── whatsapp_log.json           # Send history
    ├── news_log.json               # Discovery history
    ├── sent_whatsapp.json          # Deduplication tracker
    └── cache/                      # Search cache (30min)
```

---

## 🚀 Quick Start

### Prerequisites
- Python 3.9+
- Node.js 16+
- Spare WhatsApp number (not your main account)
- OpenAI or Gemini API key

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

### 2. Configure API Keys

Create `.env` file in project root:
```env
OPENAI_API_KEY=sk-proj-your-key-here
# OR
GEMINI_API_KEY=your-gemini-key-here
```

### 3. Configure WhatsApp

Update `selfhosted_config.json`:
```json
{
  "groupId": "YOUR_GROUP_ID@g.us",
  "botApiUrl": "http://localhost:3000"
}
```

**How to get Group ID:**
1. Get the WhatsApp invite link from your phone
2. Start the bot: `node whatsapp_selfhosted.js`
3. Use the bot's `/groups` endpoint or convert the invite link

### 4. Start Services

**Terminal 1 - WhatsApp Bot:**
```bash
node whatsapp_selfhosted.js
# Scan QR code with your spare WhatsApp number
# Wait for "WhatsApp bot ready!"
```

**Terminal 2 - Web App:**
```bash
python app.py
# Open http://localhost:5050
```

---

## 🎯 How It Works

### Complete News Flow

```
┌─────────────────────────────────────────────────────────────────┐
│  1. FETCH FROM SOURCES (38 publishers, Google News RSS)         │
│     ↓ Pipeline fetches in parallel (~15 seconds)                │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│  2. KEYWORD FILTER (Extracted Tab)                              │
│     • Award keywords: "awarded", "wins", "secured", etc.        │
│     • Construction keywords: "construction", "building", etc.   │
│     • Country keywords: "Dubai", "Riyadh", "NEOM", etc.         │
│     → Saved to extracted_news.json                              │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│  3. LLM EVALUATION (LLM Picked Tab)                             │
│     ✓ GPT-4o-mini or Gemini judges each headline               │
│     ✓ Strict rules:                                             │
│       - Must be construction/infrastructure/EPC/building        │
│       - Contract awarded/signed OR project awarded              │
│       - UAE or Saudi Arabia                                     │
│       - NOT tech, AI, software, IT deals                        │
│       - NOT statistics, market commentary                       │
│       - NOT "exploring" or "in talks"                           │
│     → Saved to llm_picks.json with keep/drop reasons            │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│  4. LIVE FEED (Live Tab)                                        │
│     • Only LLM-approved articles                                │
│     • Only today's date                                         │
│     • Only "Contract Awarded" / "Project Awarded"               │
│     → Merged into live_news.json                                │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│  5. WHATSAPP QUEUE                                              │
│     • New articles added to send_queue.json                     │
│     • Hourly sending (1 post per hour, no spam)                 │
│     • Double-check: LLM re-validates before sending             │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│  6. SEND LOG (Send Log Tab)                                     │
│     • Complete history: sent or failed                          │
│     • Tracking: source, title, date, status, group ID           │
│     → Saved to whatsapp_log.json                                │
└─────────────────────────────────────────────────────────────────┘
```

### Background Automation

- **Poller**: Checks for fresh news every 10 minutes
- **Sender**: Sends 1 article per hour from the queue
- **LLM**: Re-validates every article before sending (extra safety)

---

## 📊 Web Interface Tabs

### 1. **Search Tab**
- Manual search across all 38 sources
- Date range, keyword filtering
- Live client-side search, sorting
- Export to CSV
- "Queue for WhatsApp" button

### 2. **Extracted Tab** 🆕
- Shows all headlines after Google News + keyword filter
- **Before LLM judgment**
- Helps understand what the pipeline found
- Refresh button to re-fetch

### 3. **LLM Picked Tab** 🆕
- Shows LLM's decision for each extracted headline
- **Keep** ✓ or **Drop** ✗ with reasons
- Filter: Show All / Keep Only / Drop Only
- Transparency into LLM's thinking

### 4. **Live Tab**
- Today's approved news (LLM-filtered)
- Auto-refresh every 30 seconds
- "Post next" button for manual send
- Shows next scheduled send time
- Countdown timer

### 5. **News Log Tab**
- Complete discovery history
- All news found, sent or not
- Date, source, category tracking

### 6. **Send Log Tab**
- WhatsApp send history
- Status: ✓ Sent or ✗ Failed
- Source, date, group ID
- Error messages if failed

### 7. **Connect WhatsApp Tab**
- Bot connection status
- Group list
- QR code for re-linking

---

## 🎓 Key Features

### ✅ Strict LLM Filtering
- **No false positives**: LLM eliminates unrelated news
- **Category accuracy**: "Contract Awarded" vs "Project Awarded"
- **Construction-only**: Rejects tech, AI, software deals
- **Transparency**: See exactly why each article was kept/dropped

### ✅ Smart Sending
- **Hourly limit**: 1 post per hour (no spam)
- **Double-check**: LLM re-validates before sending
- **Date validation**: Only today's news
- **Deduplication**: No duplicate titles or hashes

### ✅ Self-Hosted WhatsApp
- **No third-party access**: Your data stays private
- **No API keys**: Uses WhatsApp Web automation
- **No ban risk**: Uses spare SIM with normal pacing
- **Session persistence**: Re-links automatically on errors

### ✅ Production-Ready
- **Clean codebase**: Unnecessary files removed
- **Error handling**: Graceful failures, logging
- **Thread-safe**: Locks prevent race conditions
- **Git-ready**: Sensitive files in .gitignore

---

## 🌐 Sources (38 Publishers)

### Construction Publications
Zawya, MEED, Construction Week Online

### UAE News
Khaleej Times, Gulf News, The National, Emirates 24/7

### Saudi News
Arab News, Saudi Gazette, Okaz, Al Riyadh

### Business
Arabian Business, Gulf Business, Trade Arabia

### Official
WAM (UAE), SPA (Saudi Arabia)

**Full list in `sources.py`**

---

## 🔧 Configuration Files

### `selfhosted_config.json`
```json
{
  "groupId": "120363123456789@g.us",
  "botApiUrl": "http://localhost:3000"
}
```

### `.env`
```env
OPENAI_API_KEY=sk-proj-...
OPENAI_MODEL=gpt-4o-mini
# OR
GEMINI_API_KEY=...
GEMINI_MODEL=gemini-2.0-flash
```

### `.gitignore`
Automatically excludes:
- WhatsApp session files
- API keys (.env)
- Generated data (JSON logs)
- Configuration (selfhosted_config.json)
- Cache and dependencies

---

## 🐛 Troubleshooting

### LLM Not Working
```bash
# Check API key in .env
cat .env
# Test manually
curl https://api.openai.com/v1/models -H "Authorization: Bearer YOUR_KEY"
```

### WhatsApp Bot Errors
```bash
# Reinstall Chrome
npx puppeteer browsers install chrome

# Clear session and restart
rm -rf .wwebjs_cache
node whatsapp_selfhosted.js
```

### Flask App Not Responding
```bash
# Kill all Python processes
taskkill /F /IM python.exe

# Restart app
python app.py
```

### No News Showing
- **Morning/Weekend**: RSS feeds update slowly
- **Default range**: App shows yesterday + today
- **LLM rejection**: Check "LLM Picked" tab for reasons

---

## 📝 Daily Workflow

1. **Morning**: Open http://localhost:5050
2. **Check "Live" tab**: See today's approved news
3. **Check "LLM Picked" tab**: Review what was filtered
4. **Check "Send Log" tab**: Verify WhatsApp posts
5. **Manual search**: Use "Search" tab for specific queries
6. **Monitor**: Background runs automatically

---

## 🛡️ Security & Privacy

✅ **All data stored locally** (no cloud services)  
✅ **Self-hosted WhatsApp** (no third-party APIs)  
✅ **Sensitive files in .gitignore** (safe for GitHub)  
✅ **API keys in .env** (never committed)  
✅ **Session files protected** (WhatsApp encryption)  

---

## 📦 Dependencies

### Python
- `flask` - Web framework
- `feedparser` - RSS parsing
- `aiohttp` - Async HTTP
- `requests` - HTTP client

### Node.js
- `whatsapp-web.js` - WhatsApp automation
- `puppeteer` - Headless Chrome
- `express` - API server
- `qrcode-terminal` - QR display

---

## 🎯 Production Deployment

### Option 1: Local PC (Recommended for testing)
```bash
# Already running - just keep terminals open
```

### Option 2: VPS (Ubuntu/Debian)
```bash
# Install PM2 for process management
npm install -g pm2

# Create PM2 config (ecosystem.config.js)
pm2 start ecosystem.config.js
pm2 save
pm2 startup
```

### Option 3: Windows Server
```bash
# Run as Windows Service using NSSM
nssm install NewsAppFlask python app.py
nssm install NewsAppWhatsApp node whatsapp_selfhosted.js
```

---

## 📄 License

Private project for internal construction news monitoring.

---

**Built for strict construction news filtering with LLM intelligence** 🏗️
