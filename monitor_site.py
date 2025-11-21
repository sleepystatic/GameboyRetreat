#!/usr/bin/env python3
"""
Site Monitoring Script
Continuously monitors your site and logs when it goes down/up
Run this alongside your application to track downtime patterns
"""

import requests
import time
import sys
from datetime import datetime
import json

# Configuration
SITE_URL = "http://localhost:5000/health"  # Change to your actual URL
CHECK_INTERVAL = 30  # seconds between checks
TIMEOUT = 10  # request timeout in seconds
LOG_FILE = "monitor.log"


class SiteMonitor:
    def __init__(self, url, check_interval=30, timeout=10):
        self.url = url
        self.check_interval = check_interval
        self.timeout = timeout
        self.is_down = False
        self.down_since = None
        self.consecutive_failures = 0
        self.consecutive_successes = 0
        self.total_checks = 0
        self.total_downtime_seconds = 0

    def log(self, message, level="INFO"):
        """Log message to console and file"""
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        log_line = f"[{timestamp}] [{level}] {message}"
        print(log_line)

        with open(LOG_FILE, 'a') as f:
            f.write(log_line + '\n')

    def check_site(self):
        """Check if site is up and return health info"""
        try:
            response = requests.get(self.url, timeout=self.timeout)
            response_time = response.elapsed.total_seconds()

            if response.status_code == 200:
                try:
                    health_data = response.json()
                    return {
                        'status': 'up',
                        'response_time': response_time,
                        'status_code': response.status_code,
                        'health': health_data
                    }
                except json.JSONDecodeError:
                    return {
                        'status': 'up',
                        'response_time': response_time,
                        'status_code': response.status_code,
                        'health': None
                    }
            else:
                return {
                    'status': 'down',
                    'response_time': response_time,
                    'status_code': response.status_code,
                    'error': f'Bad status code: {response.status_code}'
                }

        except requests.exceptions.Timeout:
            return {
                'status': 'down',
                'error': f'Timeout after {self.timeout}s'
            }
        except requests.exceptions.ConnectionError as e:
            return {
                'status': 'down',
                'error': f'Connection error: {str(e)}'
            }
        except Exception as e:
            return {
                'status': 'down',
                'error': f'Unexpected error: {str(e)}'
            }

    def run(self):
        """Main monitoring loop"""
        self.log(f"Starting site monitor for {self.url}")
        self.log(f"Check interval: {self.check_interval}s, Timeout: {self.timeout}s")
        self.log("=" * 80)

        try:
            while True:
                self.total_checks += 1
                result = self.check_site()

                if result['status'] == 'up':
                    self.consecutive_successes += 1
                    self.consecutive_failures = 0

                    # Log response time
                    response_time = result.get('response_time', 0)
                    response_ms = int(response_time * 1000)

                    # If we just recovered from downtime
                    if self.is_down:
                        downtime_duration = datetime.now() - self.down_since
                        downtime_seconds = int(downtime_duration.total_seconds())
                        self.total_downtime_seconds += downtime_seconds

                        self.log(
                            f"🟢 SITE RECOVERED! Was down for {downtime_duration} "
                            f"({downtime_seconds}s)",
                            "SUCCESS"
                        )
                        self.is_down = False
                        self.down_since = None

                    # Log normal checks less frequently unless there's an issue
                    if self.total_checks % 10 == 0 or response_ms > 1000:
                        health = result.get('health', {})
                        uptime = health.get('uptime_human', 'unknown') if health else 'unknown'
                        self.log(
                            f"✓ Site UP (response: {response_ms}ms, "
                            f"app uptime: {uptime}, "
                            f"checks: {self.total_checks})"
                        )

                else:
                    self.consecutive_failures += 1
                    self.consecutive_successes = 0

                    # Mark as down if this is the first failure
                    if not self.is_down:
                        self.is_down = True
                        self.down_since = datetime.now()
                        error = result.get('error', 'Unknown error')
                        self.log(
                            f"🔴 SITE DOWN! Error: {error}",
                            "ERROR"
                        )
                    else:
                        # Log continued downtime
                        downtime_so_far = datetime.now() - self.down_since
                        error = result.get('error', 'Unknown error')
                        self.log(
                            f"🔴 Still down ({downtime_so_far} elapsed). "
                            f"Consecutive failures: {self.consecutive_failures}. "
                            f"Error: {error}",
                            "ERROR"
                        )

                # Print summary every hour
                if self.total_checks % (3600 // self.check_interval) == 0:
                    uptime_pct = ((self.total_checks - self.consecutive_failures) / self.total_checks) * 100
                    self.log("=" * 80)
                    self.log(
                        f"HOURLY SUMMARY: {self.total_checks} checks, "
                        f"{uptime_pct:.2f}% uptime, "
                        f"{self.total_downtime_seconds}s total downtime",
                        "INFO"
                    )
                    self.log("=" * 80)

                # Wait before next check
                time.sleep(self.check_interval)

        except KeyboardInterrupt:
            self.log("\nMonitoring stopped by user")
            self.print_summary()

    def print_summary(self):
        """Print final summary"""
        self.log("=" * 80)
        self.log("MONITORING SUMMARY")
        self.log(f"Total checks: {self.total_checks}")
        self.log(f"Total downtime: {self.total_downtime_seconds}s ({self.total_downtime_seconds // 60}m)")

        if self.total_checks > 0:
            uptime_pct = ((self.total_checks - self.consecutive_failures) / self.total_checks) * 100
            self.log(f"Uptime: {uptime_pct:.2f}%")
        self.log("=" * 80)


def main():
    # Allow URL override from command line
    url = SITE_URL
    if len(sys.argv) > 1:
        url = sys.argv[1]

    monitor = SiteMonitor(url, CHECK_INTERVAL, TIMEOUT)

    print(f"\n{'='*80}")
    print("  GameBoy Retreat - Site Monitor")
    print(f"{'='*80}")
    print(f"\nMonitoring: {url}")
    print(f"Logs will be written to: {LOG_FILE}")
    print("\nPress Ctrl+C to stop\n")
    print(f"{'='*80}\n")

    monitor.run()


if __name__ == '__main__':
    main()
