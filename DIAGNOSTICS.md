# Diagnostics & Monitoring Guide

This guide will help you diagnose your site's downtime issues.

## 🔧 Diagnostic Tools Included

### 1. Application Logs (`app.log`)
- All requests are now logged with timestamps
- Email sending operations tracked
- Database operations monitored
- Errors logged with full stack traces

**Location**: `app.log` (created automatically when app runs)

### 2. Health Check Endpoint (`/health`)
- **URL**: `http://your-site.com/health`
- Returns JSON with:
  - Application status
  - Uptime (how long since app started)
  - Database connectivity
  - Environment configuration check

**Usage**:
```bash
curl http://localhost:5000/health
```

### 3. Health Check Script (`health_check.py`)
Run this to check the current state of your server:

```bash
python3 health_check.py
```

**What it checks**:
- System resources (CPU, memory, disk)
- Running Python/Flask/Gunicorn processes
- Database connectivity
- Environment variables
- Network ports
- Recent log files
- Cron jobs that might restart services

### 4. Site Monitor (`monitor_site.py`)
Continuously monitors your site and logs when it goes down:

```bash
# Monitor localhost (default)
python3 monitor_site.py

# Monitor production site
python3 monitor_site.py https://your-site.com/health
```

**Features**:
- Checks every 30 seconds
- Logs to `monitor.log`
- Alerts when site goes down/up
- Tracks downtime duration
- Provides hourly summaries

## 🚀 Quick Diagnosis Steps

### Step 1: Check if your app is running
```bash
ps aux | grep python
ps aux | grep gunicorn
```

### Step 2: Run the health check
```bash
python3 health_check.py
```

### Step 3: Check application logs
```bash
tail -f app.log
```

### Step 4: Start the monitor (let it run for a few hours)
```bash
python3 monitor_site.py https://your-site.com/health
```

### Step 5: Check system logs
```bash
# On Ubuntu/Debian
sudo tail -f /var/log/syslog | grep -i python

# Check for OOM kills
sudo dmesg | grep -i "killed process"

# Check systemd logs if using systemd
sudo journalctl -u your-service-name -f
```

## 📊 Understanding the Downtime Pattern

### If downtime is exactly every hour:
- Check for cron jobs: `crontab -l`
- Check systemd timers: `systemctl list-timers`
- Look for scheduled tasks in logs during downtime

### If downtime is random:
- Monitor system resources with `health_check.py`
- Check for memory issues (OOM killer)
- Look for Gunicorn worker timeouts
- Check database locks

### If downtime is ~10 minutes:
- Check Gunicorn worker timeout settings
- Look for long-running requests
- Check for database lock timeout
- Verify no blocking I/O operations

## 🔍 Common Causes & Solutions

### High Memory Usage
**Symptoms**: App crashes or becomes unresponsive
**Check**: `health_check.py` will show memory usage
**Solution**:
- Increase server RAM
- Add swap space
- Reduce Gunicorn workers
- Check for memory leaks

### Gunicorn Worker Timeout
**Symptoms**: 502/503 errors after ~30-60 seconds
**Check**: Look for "Worker timeout" in logs
**Solution**: Increase timeout in gunicorn config:
```bash
gunicorn --timeout 120 --workers 4 app:app
```

### Database Locks
**Symptoms**: SQLite database locked errors
**Check**: Look for "database is locked" in `app.log`
**Solution**:
- Increase timeout: `sqlite3.connect('db', timeout=30)`
- Consider PostgreSQL for production
- Check for long-running transactions

### Network Issues
**Symptoms**: Connection refused, timeouts
**Check**: `netstat -tlnp | grep :5000`
**Solution**:
- Verify app is binding to correct interface
- Check firewall rules
- Verify reverse proxy configuration

## 📝 Information to Gather

When asking for help, provide:

1. **Output from health check**:
   ```bash
   python3 health_check.py > health_check_output.txt
   ```

2. **Recent application logs**:
   ```bash
   tail -100 app.log > recent_logs.txt
   ```

3. **Monitor logs during downtime**:
   ```bash
   grep "SITE DOWN" monitor.log
   ```

4. **Deployment information**:
   - Hosting provider
   - Server specifications (RAM, CPU)
   - Gunicorn configuration
   - Reverse proxy setup (nginx/Apache config)

5. **Exact error messages** from your uptime robot

## 🛠️ Installation

Install dependencies:
```bash
pip install -r requirements.txt
```

Make scripts executable:
```bash
chmod +x health_check.py monitor_site.py
```

## ⚙️ Production Deployment Checklist

- [ ] Application logs to file (`app.log` is created automatically)
- [ ] Monitoring script running in background
- [ ] Health check endpoint accessible
- [ ] Gunicorn configured with proper worker count
- [ ] Timeout settings configured
- [ ] Database properly configured
- [ ] Environment variables set
- [ ] Reverse proxy configured correctly
- [ ] Firewall rules allow traffic
- [ ] SSL certificate valid (if using HTTPS)
