# Quick Test Guide - Verify Everything Works

## ✅ What Was Done

Your project is now **production-ready** with:

1. ✅ **Strict LLM filtering** - Only "Project Awarded" and "Contract Awarded" construction news
2. ✅ **Complete UI transparency** - See Extracted → LLM Picked → Live → Sent
3. ✅ **Clean codebase** - Removed 10 unnecessary files
4. ✅ **Updated documentation** - README, SYSTEM_FLOW, CHANGES_SUMMARY

---

## 🧪 Test the System (Step by Step)

### Step 1: Check Services Status

```powershell
# Check if Python (Flask) is running
Get-Process python -ErrorAction SilentlyContinue

# Check if Node (WhatsApp bot) is running
Get-Process node -ErrorAction SilentlyContinue
```

**If NOT running:**

```bash
# Terminal 1: Start WhatsApp Bot
node whatsapp_selfhosted.js

# Terminal 2: Start Flask App
python app.py
```

### Step 2: Open the UI

1. Open browser: **http://localhost:5050**
2. You should see the main page with 7 tabs

### Step 3: Test Tab by Tab

#### **Tab 1: Search**
1. Click "Search" tab (should be active by default)
2. Leave dates empty (or set to yesterday + today)
3. Leave keyword empty
4. Click "Search" button
5. Wait ~15 seconds
6. **Expected:** See search results with articles

#### **Tab 2: Extracted** ⭐ NEW
1. Click "Extracted" tab
2. Click "Refresh" button
3. Wait ~15 seconds
4. **Expected:** See raw headlines after keyword filter
5. **Count:** ~50-200 articles (before LLM judgment)
6. **Columns:** Title, Source, Date, Initial Category

**This shows what the pipeline found BEFORE LLM filtering.**

#### **Tab 3: LLM Picked** ⭐ NEW
1. Click "LLM Picked" tab
2. Click "Refresh" button
3. Wait ~5-10 seconds (LLM evaluation)
4. **Expected:** See every article with LLM decision

**Filter buttons:**
- Click "Show All" - See all articles
- Click "Keep Only" - See only approved (✓)
- Click "Drop Only" - See only rejected (✗)

**What to look for:**
- ✓ **Keep** articles should be construction awards only
- ✗ **Drop** articles should show rejection reasons like:
  - "No construction mentioned, IT contract"
  - "Market statistics, not an actual award"
  - "Exploring only, not signed/awarded yet"

**This shows LLM transparency - see WHY articles were kept or dropped.**

#### **Tab 4: Live**
1. Click "Live" tab
2. **Expected:** See today's approved news
3. **Count:** ~5-15 articles (only LLM-approved + today + awards)
4. **Auto-refresh:** Page refreshes every 30 seconds
5. **Next send:** See countdown timer for next WhatsApp post

**This is your monitoring dashboard for today's news.**

#### **Tab 5: News Log**
1. Click "News Log" tab
2. **Expected:** See all discovered news (sent or not)
3. **History:** Complete archive, not just today

#### **Tab 6: Send Log**
1. Click "Send Log" tab
2. **Expected:** See WhatsApp send history
3. **Columns:** Source, News, Date, Sent Date, Status, Group ID
4. **Status:**
   - ✓ **Sent** - Successfully posted to WhatsApp
   - ✗ **Failed** - Failed with error message

#### **Tab 7: Connect WhatsApp**
1. Click "Connect WhatsApp" tab
2. **Expected:** See bot status
3. **Status should show:** "✓ Connected" or "QR Code" for linking

---

## 🔍 Verify LLM is Working

### Test 1: Check .env File

```powershell
cat .env
```

**Expected output:**
```
OPENAI_API_KEY=sk-proj-...
```

**If missing:** Create `.env` file with your API key.

### Test 2: Check LLM Picks Tab

1. Go to Tab 3 (LLM Picked)
2. Click "Refresh"
3. Wait for results
4. **Expected:** See articles with decisions (Keep/Drop)

**If you see "No evaluations yet":**
- Check if `.env` has API key
- Check if Tab 2 (Extracted) has articles
- Check browser console for errors (F12)

### Test 3: Check Article Quality

1. Go to Tab 3 (LLM Picked)
2. Click "Keep Only" button
3. **Expected:** All kept articles should be:
   - ✅ Construction/infrastructure/EPC/building
   - ✅ Contract awarded OR project awarded
   - ✅ UAE or Saudi Arabia

4. Click "Drop Only" button
5. **Expected:** Dropped articles should show reasons like:
   - ✗ Tech/AI/software deals
   - ✗ Statistics/commentary
   - ✗ "Exploring" deals
   - ✗ Non-construction topics

**If bad articles are kept:** LLM prompt needs to be stricter (edit `llm_judge.py`)

**If good articles are dropped:** LLM prompt needs to be relaxed (edit `llm_judge.py`)

---

## 🎯 Verify Complete Flow

### Automatic Background Flow (Runs every 10 minutes)

```
1. FETCH → Tab 2: Extracted (~50-200 articles)
                    ↓
2. LLM JUDGE → Tab 3: LLM Picked (Keep ✓ / Drop ✗ + reasons)
                    ↓
3. FILTER → Tab 4: Live (~5-15 approved articles)
                    ↓
4. QUEUE → Send Queue (hourly sending)
                    ↓
5. SEND → Tab 6: Send Log (✓ Sent / ✗ Failed)
```

### Test the Flow Manually

1. **Start fresh:** 
   - Delete `extracted_news.json`
   - Delete `llm_picks.json`
   - Delete `live_news.json`

2. **Go to Tab 2 (Extracted):**
   - Click "Refresh"
   - Wait ~15 seconds
   - **Verify:** Articles appear

3. **Go to Tab 3 (LLM Picked):**
   - Click "Refresh"
   - Wait ~5-10 seconds
   - **Verify:** Decisions appear (Keep/Drop)

4. **Go to Tab 4 (Live):**
   - Wait 5 seconds for auto-refresh
   - **Verify:** Only approved articles appear

5. **Check Tab 6 (Send Log):**
   - Wait 1 hour for automatic send
   - **Verify:** New entry appears with status

---

## 🐛 Common Issues & Solutions

### Issue 1: Tab 2/3 Shows "Loading..."

**Cause:** Flask app not running or backend error

**Solution:**
```bash
# Check Flask logs in Terminal 2
# Look for errors

# Restart Flask app
# Ctrl+C in Terminal 2
python app.py
```

### Issue 2: Tab 3 Shows "No evaluations yet"

**Cause:** LLM not configured or API key invalid

**Solution:**
```bash
# Check .env file
cat .env

# Test API key
curl https://api.openai.com/v1/models -H "Authorization: Bearer YOUR_KEY"

# If invalid, update .env with correct key
```

### Issue 3: Tab 4 (Live) is Empty

**Cause 1:** No today's news (normal in morning/weekend)

**Solution:** Wait until afternoon or check Tab 2/3 to see if anything was extracted

**Cause 2:** LLM rejecting all articles (too strict)

**Solution:**
1. Go to Tab 3 (LLM Picked)
2. Click "Drop Only"
3. Check rejection reasons
4. If too many good articles are dropped, relax LLM prompt in `llm_judge.py`

### Issue 4: WhatsApp Not Sending

**Cause:** Bot not connected or group ID wrong

**Solution:**
```bash
# Check Tab 7 (Connect WhatsApp)
# Verify bot status: "✓ Connected"

# If not connected, restart bot
# Terminal 1: Ctrl+C
node whatsapp_selfhosted.js

# Scan QR code with spare WhatsApp number
```

---

## 📊 Expected Numbers

### Normal Day (Weekday, Afternoon)

| Tab | Count | Description |
|-----|-------|-------------|
| **Tab 2: Extracted** | 50-200 | Raw headlines after keyword filter |
| **Tab 3: LLM Keep** | 10-25 | LLM-approved articles |
| **Tab 3: LLM Drop** | 40-175 | LLM-rejected articles |
| **Tab 4: Live** | 5-15 | Today's approved + award categories |
| **Tab 6: Send Log** | 5-15 | Sent to WhatsApp (1 per hour) |

### Quiet Day (Weekend, Morning, Low News Day)

| Tab | Count | Description |
|-----|-------|-------------|
| **Tab 2: Extracted** | 10-50 | Less news available |
| **Tab 3: LLM Keep** | 2-10 | Fewer approvals |
| **Tab 3: LLM Drop** | 8-40 | Fewer rejections |
| **Tab 4: Live** | 0-5 | Very few today's articles |
| **Tab 6: Send Log** | 0-5 | Less to send |

**Note:** Weekend/morning numbers are normal due to RSS feed delays.

---

## ✅ Success Checklist

Run through this checklist to verify everything works:

- [ ] Both services running (Flask + WhatsApp bot)
- [ ] UI opens at http://localhost:5050
- [ ] Tab 1 (Search) shows results when searching
- [ ] Tab 2 (Extracted) shows raw headlines
- [ ] Tab 3 (LLM Picked) shows Keep/Drop decisions with reasons
- [ ] Tab 4 (Live) shows today's approved news
- [ ] Tab 5 (News Log) shows discovery history
- [ ] Tab 6 (Send Log) shows WhatsApp history
- [ ] Tab 7 (Connect WhatsApp) shows "✓ Connected"
- [ ] LLM is rejecting non-construction articles (check Tab 3 "Drop Only")
- [ ] Live feed only shows construction awards (check Tab 4)
- [ ] WhatsApp sends are logged (check Tab 6)

---

## 🎯 Final Test: Show Your Manager

1. **Open Tab 4 (Live)**
   - Show today's approved news
   - All articles should be construction awards only
   - No tech deals, no statistics, no "exploring" articles

2. **Open Tab 3 (LLM Picked)**
   - Click "Drop Only"
   - Show examples of what LLM rejected
   - Prove that irrelevant articles are being filtered out

3. **Open Tab 6 (Send Log)**
   - Show what's been sent to WhatsApp
   - All should be legitimate construction awards

**Your manager should be satisfied! 👍**

---

## 📚 Documentation Files

After testing, read these for complete understanding:

1. **README.md** - Complete project documentation
2. **SYSTEM_FLOW.md** - Visual flow diagram (read this next!)
3. **CHANGES_SUMMARY.md** - What was changed today
4. **QUICK_TEST_GUIDE.md** - This file (testing guide)

---

## 🚀 Next Steps After Testing

1. **Monitor for 24 hours:**
   - Let background automation run
   - Check Tab 4 (Live) periodically
   - Verify Tab 6 (Send Log) shows hourly sends

2. **Adjust LLM if needed:**
   - If too strict: Edit `llm_judge.py` SYSTEM_PROMPT
   - If too loose: Add more rejection rules

3. **Add more sources if needed:**
   - Edit `sources.py`
   - Add more UAE/Saudi news outlets

4. **Deploy to VPS (optional):**
   - See README.md "Production Deployment" section
   - Use PM2 or Windows Service for 24/7 running

---

**Your construction news monitoring system is ready to go! 🎉**

No more irrelevant articles for your manager! 💪
