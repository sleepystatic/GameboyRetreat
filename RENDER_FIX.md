# 🎯 Render Downtime Fix

## Root Cause Identified

Your logs show Render is **restarting your application** with TERM signals:
```
[2025-11-21 19:54:21] Handling signal: term → Shutting down
[2025-11-21 20:31:12] Starting gunicorn 21.2.0 (restarted)
```

**This is NOT a code problem** - it's a Render configuration issue.

## Why Render Was Restarting Your App

### 1. ❌ No Health Check Configured (Primary Cause)
- Render expects apps to respond to health checks
- Your app didn't have a `/health` endpoint until now
- Render was timing out and restarting the app thinking it was broken

### 2. ⚠️ Single Gunicorn Worker
- Running only 1 worker (seen in logs: "Booting worker with pid: 39")
- If that worker hangs or crashes, entire app goes down
- No redundancy

### 3. 📊 Possible Memory Limits
- Free tier limited to 512MB RAM
- SQLite + Flask + email operations might exceed limits
- Render kills the process when limits are hit

## ✅ The Fix

### Step 1: Configure Health Check in Render Dashboard

**Go to your Render service settings:**

1. Navigate to your service in Render dashboard
2. Go to **Settings** → **Health & Alerts**
3. Set **Health Check Path** to: `/health`
4. Set **Health Check Timeout** to: `10` seconds
5. Save changes

![Health Check Configuration](https://render.com/docs/health-checks)

### Step 2: Update Your Start Command

**In Render Dashboard → Settings → Build & Deploy:**

Change your **Start Command** to:
```bash
gunicorn --bind 0.0.0.0:$PORT --workers 2 --timeout 60 --worker-class sync --max-requests 1000 --max-requests-jitter 50 --access-logfile - --error-logfile - app:app
```

**What this does:**
- `--workers 2`: Run 2 workers for redundancy (free tier can handle this)
- `--timeout 60`: Give workers 60 seconds before timeout (default is 30)
- `--max-requests 1000`: Restart workers after 1000 requests (prevents memory leaks)
- `--max-requests-jitter 50`: Randomize restart to avoid all workers restarting at once
- `--access-logfile -`: Log requests to stdout
- `--error-logfile -`: Log errors to stdout

### Step 3: Use render.yaml (Optional but Recommended)

I've created a `render.yaml` file that automatically configures everything:

1. Commit the `render.yaml` file to your repo
2. In Render dashboard, go to **Settings** → **Build & Deploy**
3. Ensure **Auto-Deploy** is enabled
4. Render will automatically use the `render.yaml` configuration

### Step 4: Verify Environment Variables

Ensure these are set in Render dashboard under **Environment**:
- ✓ `STRIPE_SECRET_KEY`
- ✓ `STRIPE_PUBLISHABLE_KEY`
- ✓ `STRIPE_WEBHOOK_SECRET`
- ✓ `ADMIN_EMAIL`
- ✓ `GMAIL_USER`
- ✓ `GMAIL_APP_PASSWORD`

## 🧪 Testing the Fix

### 1. Deploy the Changes
```bash
git push
```

Render will auto-deploy if you have auto-deploy enabled.

### 2. Check Health Endpoint
```bash
curl https://your-app.onrender.com/health
```

You should see:
```json
{
  "status": "healthy",
  "timestamp": "2025-11-21T21:30:00",
  "uptime_seconds": 123,
  "uptime_human": "0:02:03",
  "database": "healthy",
  "checks": {
    "database_connection": true,
    "environment_vars": true
  }
}
```

### 3. Monitor the Logs

In Render dashboard, check **Logs**. You should now see:
```
[INFO] GET /health from 10.x.x.x
[INFO] GET /health -> 200
```

No more TERM signals and restarts!

### 4. Wait for Next Uptime Check

Your uptime robot should now see consistent uptime with no more 10-minute downtimes.

## 📊 Understanding Render's Behavior

### Free Tier Limitations
- **512MB RAM limit** - Exceeding this causes restarts
- **Spin down after 15 min inactivity** - First request after spin-down takes longer
- **Health checks required** - Missing health checks = restarts

### Paid Tiers Benefits
If downtime continues and you're on free tier, consider upgrading to Starter ($7/mo):
- **512MB RAM** (same, but more stable)
- **No spin-downs** - Always running
- **Better health check tolerance**
- **Persistent disk included**

## 🔍 Monitoring Going Forward

### Check Render Metrics
In your Render dashboard, monitor:
- **Memory usage** - Should stay under 400MB on free tier
- **CPU usage** - Spikes might indicate issues
- **Response times** - `/health` should respond in <100ms
- **Restart events** - Should be zero after this fix

### Application Logs
Your `app.log` now captures all requests:
```
2025-11-21 21:30:00 [INFO] GET /health from 10.0.0.1
2025-11-21 21:30:00 [INFO] GET /health -> 200
```

## 🚨 If Problems Persist

### Check Memory Usage
If restarts continue, you might be hitting memory limits:

1. In Render dashboard, check **Metrics** → **Memory**
2. If consistently near 512MB, you need to:
   - Reduce Gunicorn workers to 1: `--workers 1`
   - Upgrade to paid tier with more RAM
   - Optimize your application (unlikely needed for this app)

### Check for Long-Running Requests
If you see "Worker timeout" in logs:
- A request is taking longer than 60 seconds
- Check `app.log` for slow operations
- Increase timeout: `--timeout 120`

### SQLite Database Location
On Render, you need persistent disk for SQLite:
- The `render.yaml` configures this automatically
- Or manually add a disk in Render dashboard under **Disks**
- Mount path: `/opt/render/project/src`

## ✅ Expected Outcome

After this fix:
- ✅ No more TERM signal restarts
- ✅ Health checks respond successfully
- ✅ Uptime robot shows 99.9%+ uptime
- ✅ 10-minute downtime windows eliminated
- ✅ Logs show consistent operation

## 🆘 Still Need Help?

If issues persist after this fix, gather:
1. Screenshot of Render **Metrics** page (Memory, CPU over 24h)
2. Recent logs from Render dashboard showing any TERM signals
3. Output from your uptime robot showing exact error codes
4. Render service tier (Free, Starter, etc.)

---

**TL;DR**: Configure `/health` as your health check path in Render dashboard, use 2 Gunicorn workers, and increase timeout to 60s. This will stop the restarts.
