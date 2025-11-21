#!/usr/bin/env python3
"""
Health Check and Diagnostic Script
Run this to check the current state of your application and system
"""

import os
import sys
import sqlite3
import psutil
import subprocess
from datetime import datetime

def print_section(title):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}")

def check_system_resources():
    """Check CPU, memory, and disk usage"""
    print_section("SYSTEM RESOURCES")

    # CPU
    cpu_percent = psutil.cpu_percent(interval=1)
    print(f"CPU Usage: {cpu_percent}%")

    # Memory
    mem = psutil.virtual_memory()
    print(f"Memory Usage: {mem.percent}% ({mem.used / 1024**3:.2f}GB / {mem.total / 1024**3:.2f}GB)")
    print(f"Memory Available: {mem.available / 1024**3:.2f}GB")

    # Disk
    disk = psutil.disk_usage('/')
    print(f"Disk Usage: {disk.percent}% ({disk.used / 1024**3:.2f}GB / {disk.total / 1024**3:.2f}GB)")

    # Load average
    load1, load5, load15 = psutil.getloadavg()
    cpu_count = psutil.cpu_count()
    print(f"Load Average: {load1:.2f} (1m), {load5:.2f} (5m), {load15:.2f} (15m)")
    print(f"CPU Cores: {cpu_count}")

    return mem.percent > 80 or cpu_percent > 80

def check_python_processes():
    """Find all Python/Flask/Gunicorn processes"""
    print_section("PYTHON PROCESSES")

    found_processes = False
    for proc in psutil.process_iter(['pid', 'name', 'cmdline', 'cpu_percent', 'memory_percent', 'create_time']):
        try:
            if proc.info['cmdline']:
                cmdline = ' '.join(proc.info['cmdline'])
                if any(keyword in cmdline.lower() for keyword in ['python', 'flask', 'gunicorn', 'app.py']):
                    found_processes = True
                    uptime = datetime.now() - datetime.fromtimestamp(proc.info['create_time'])
                    print(f"\nPID: {proc.info['pid']}")
                    print(f"  Command: {cmdline[:100]}")
                    print(f"  CPU: {proc.info['cpu_percent']}%")
                    print(f"  Memory: {proc.info['memory_percent']:.2f}%")
                    print(f"  Uptime: {uptime}")
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass

    if not found_processes:
        print("⚠️  No Python/Flask/Gunicorn processes found!")
        return False
    return True

def check_database():
    """Check database connectivity and size"""
    print_section("DATABASE STATUS")

    db_path = 'submissions.db'
    if os.path.exists(db_path):
        size = os.path.getsize(db_path) / 1024
        print(f"Database file: {db_path}")
        print(f"Database size: {size:.2f} KB")

        try:
            conn = sqlite3.connect(db_path, timeout=5)
            c = conn.cursor()
            c.execute('SELECT COUNT(*) FROM sellers')
            count = c.fetchone()[0]
            print(f"Total submissions: {count}")

            # Check for locks
            c.execute("PRAGMA database_list")
            print(f"Database accessible: ✓")
            conn.close()
            return True
        except Exception as e:
            print(f"⚠️  Database error: {e}")
            return False
    else:
        print(f"⚠️  Database file not found: {db_path}")
        return False

def check_environment():
    """Check environment variables and configuration"""
    print_section("ENVIRONMENT CONFIGURATION")

    required_vars = [
        'STRIPE_SECRET_KEY',
        'STRIPE_PUBLISHABLE_KEY',
        'ADMIN_EMAIL',
        'GMAIL_USER',
        'GMAIL_APP_PASSWORD'
    ]

    missing = []
    for var in required_vars:
        if os.getenv(var):
            print(f"✓ {var}: {'*' * 10} (set)")
        else:
            print(f"✗ {var}: NOT SET")
            missing.append(var)

    return len(missing) == 0

def check_network_ports():
    """Check what ports are listening"""
    print_section("NETWORK PORTS")

    listening = []
    for conn in psutil.net_connections(kind='inet'):
        if conn.status == 'LISTEN':
            listening.append((conn.laddr.port, conn.pid))

    # Look for common Flask/Gunicorn ports
    common_ports = [5000, 8000, 8080, 80, 443]
    for port in common_ports:
        matches = [pid for p, pid in listening if p == port]
        if matches:
            print(f"✓ Port {port}: LISTENING (PID: {matches[0]})")
        else:
            print(f"  Port {port}: Not in use")

    return len(listening) > 0

def check_logs():
    """Look for recent error logs"""
    print_section("RECENT LOG FILES")

    log_locations = [
        '/var/log/syslog',
        '/var/log/messages',
        '/var/log/nginx/error.log',
        '/var/log/apache2/error.log',
        'app.log',
        'error.log'
    ]

    found_logs = []
    for log_path in log_locations:
        if os.path.exists(log_path):
            try:
                size = os.path.getsize(log_path) / 1024
                mtime = datetime.fromtimestamp(os.path.getmtime(log_path))
                print(f"Found: {log_path} ({size:.2f}KB, modified: {mtime})")
                found_logs.append(log_path)
            except PermissionError:
                print(f"Found: {log_path} (no read permission)")

    return found_logs

def check_cron_jobs():
    """Check for cron jobs that might restart the service"""
    print_section("SCHEDULED TASKS")

    try:
        # User crontab
        result = subprocess.run(['crontab', '-l'], capture_output=True, text=True)
        if result.returncode == 0 and result.stdout.strip():
            print("User crontab:")
            print(result.stdout)
        else:
            print("No user crontab entries")
    except Exception as e:
        print(f"Could not check crontab: {e}")

    # System cron files
    cron_dirs = ['/etc/cron.hourly', '/etc/cron.d']
    for cron_dir in cron_dirs:
        if os.path.exists(cron_dir):
            files = os.listdir(cron_dir)
            if files:
                print(f"\n{cron_dir} contains: {', '.join(files)}")

def main():
    print(f"\n{'#'*60}")
    print(f"  GameBoy Retreat - Health Check")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'#'*60}")

    issues = []

    # Run all checks
    if check_system_resources():
        issues.append("High system resource usage")

    if not check_python_processes():
        issues.append("No application processes running")

    if not check_database():
        issues.append("Database connectivity issues")

    if not check_environment():
        issues.append("Missing environment variables")

    if not check_network_ports():
        issues.append("No listening ports found")

    check_logs()
    check_cron_jobs()

    # Summary
    print_section("HEALTH CHECK SUMMARY")
    if issues:
        print("⚠️  ISSUES FOUND:")
        for issue in issues:
            print(f"  - {issue}")
    else:
        print("✓ All checks passed!")

    print(f"\n{'#'*60}\n")

if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print("\n\nHealth check interrupted")
        sys.exit(0)
    except Exception as e:
        print(f"\n\n⚠️  Error running health check: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
