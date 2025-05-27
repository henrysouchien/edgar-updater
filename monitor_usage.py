#!/usr/bin/env python3

import requests
import json
from datetime import datetime, timedelta, UTC
from collections import Counter, defaultdict
from typing import Dict, List, Any
import os
from dotenv import load_dotenv

# Constants
ADMIN_KEY = "EfhTAhqvznJsnkdV-GL7kGov3ptWfnzxpIu6xkxlW6A"
BASE_URL = "https://www.financialmodelupdater.com"

class AdminMonitor:
    def __init__(self):
        self.base_url = BASE_URL
        self.admin_key = ADMIN_KEY

    def _make_request(self, endpoint: str, params: Dict = None) -> Dict:
        """Make authenticated request to admin endpoint"""
        if params is None:
            params = {}
        params['key'] = self.admin_key  # Use key for admin auth
        
        response = requests.get(f"{self.base_url}{endpoint}", params=params)
        response.raise_for_status()
        return response.json()

    def get_usage_summary(self) -> Dict:
        """Get high-level usage statistics"""
        return self._make_request('/admin/usage_summary')

    def get_key_usage(self, check_key: str = 'public') -> Dict:
        """Get usage statistics for specific key type"""
        return self._make_request('/admin/check_key_usage', {'check_key': check_key})

    def resolve_key_to_email(self, api_key: str) -> str:
        """Resolve API key to email address"""
        try:
            # For resolve_key endpoint, we need to use token instead of key
            response = requests.get(
                f"{self.base_url}/admin/resolve_key",
                params={
                    'token': self.admin_key,  # Use token for admin auth
                    'key': api_key  # The API key to resolve
                }
            )
            response.raise_for_status()
            data = response.json()
            if data.get('status') == 'success':
                return data.get('email', 'Unknown')
            return 'Unknown'
        except Exception as e:
            return 'Unknown'

    def format_timestamp(self, ts: str) -> str:
        """Format timestamp for display"""
        dt = datetime.fromisoformat(ts.replace('Z', '+00:00'))
        return dt.strftime('%Y-%m-%dT%H:%M')

    def print_section_header(self, title: str):
        """Print a formatted section header"""
        print(f"\n{'='*50}")
        print(f"🔹 {title}")
        print('='*50)

    def display_usage_summary(self, data: Dict):
        """Display usage summary statistics"""
        self.print_section_header("1. USAGE SUMMARY")
        
        summary = data.get('summary', {})
        print(f"Total Requests: {summary.get('total_requests', 0):,}")
        print(f"Successful Requests: {summary.get('successful_requests', 0):,}")
        print(f"Rate-Limited Requests: {summary.get('rate_limited_requests', 0):,}")
        print(f"Cache Hits: {summary.get('cache_hits', 0):,}")
        
        print("\nUsage by Tier:")
        by_tier = summary.get('by_tier', {})
        print(f"  • Public: {by_tier.get('public', 0):,}")
        print(f"  • Registered: {by_tier.get('registered', 0):,}")
        print(f"  • Paid: {by_tier.get('paid', 0):,}")

    def display_top_keys(self, data: Dict):
        """Display top API keys by usage (registered and paid) in the last 24 hours, with emails"""
        self.print_section_header("2. TOP KEYS (Last 24h)")

        entries = data.get('data', [])
        now = datetime.now(UTC)
        registered_keys = Counter()
        paid_keys = Counter()
        key_to_time = {}

        for entry in entries:
            # Get all requests for this entry
            requests = entry.get('related_requests', [])
            
            for req in requests:
                key = req.get('key')
                tier = req.get('tier')
                timestamp = req.get('timestamp')
                if not key or not tier or not timestamp:
                    continue
                try:
                    # Parse timestamp and ensure it's in UTC
                    ts = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
                    if now - ts > timedelta(hours=24):
                        continue
                    if tier == 'registered':
                        registered_keys[key] += 1
                    elif tier == 'paid':
                        paid_keys[key] += 1
                    key_to_time[key] = ts
                except Exception as e:
                    print(f"Error processing timestamp for key {key}: {e}")
                    continue

        # Display top registered keys
        print("\nTop Registered Keys:")
        if registered_keys:
            for key, count in registered_keys.most_common(5):
                email = self.resolve_key_to_email(key)
                last_active = key_to_time.get(key)
                last_active_str = last_active.strftime('%Y-%m-%d %H:%M') if last_active else 'N/A'
                print(f"{key[:8]}... → {count:,} requests (Email: {email}, Last: {last_active_str})")
        else:
            print("No registered key activity found")

        # Display top paid keys
        print("\nTop Paid Keys:")
        if paid_keys:
            for key, count in paid_keys.most_common(5):
                email = self.resolve_key_to_email(key)
                last_active = key_to_time.get(key)
                last_active_str = last_active.strftime('%Y-%m-%d %H:%M') if last_active else 'N/A'
                print(f"{key[:8]}... → {count:,} requests (Email: {email}, Last: {last_active_str})")
        else:
            print("No paid key activity found")

    def display_public_ips(self, data: Dict):
        """Display public IPs with high usage"""
        self.print_section_header("3. PUBLIC IPs > 10 USES")
        
        public_data = self.get_key_usage('public')
        ip_counts = public_data.get('ip_counts', {})
        
        # Sort IPs by usage count
        sorted_ips = sorted(ip_counts.items(), key=lambda x: x[1], reverse=True)
        
        for ip, count in sorted_ips:
            if count > 10:  # Show IPs with more than 10 uses
                print(f"{ip} → {count:,} requests")

    def display_errors(self, data: Dict):
        """Display recent errors"""
        self.print_section_header("4. ERRORS (Last 24h)")
        
        # Get errors from the last 24 hours
        now = datetime.now()
        errors = []
        
        for entry in data.get('data', []):
            if entry.get('error'):
                error_time = datetime.fromisoformat(entry['error'].get('timestamp', '').replace('Z', '+00:00'))
                if now - error_time <= timedelta(hours=24):
                    errors.append(entry['error'])
        
        if not errors:
            print("No errors in the last 24 hours")
            return
            
        for error in errors:
            timestamp = self.format_timestamp(error.get('timestamp', ''))
            key = error.get('key', 'Unknown')
            message = error.get('error', 'Unknown error')
            ticker = error.get('ticker', 'N/A')
            
            print(f"[{timestamp}] {key} → {message} (Ticker: {ticker})")

    def display_upgrade_candidates(self, data: Dict):
        """Display potential upgrade candidates from registered users"""
        self.print_section_header("5. ⚠️ UPGRADE CANDIDATES")
        
        # Track user activity
        user_activity = defaultdict(lambda: {
            'total_requests': 0,
            'successful_requests': 0,
            'rate_limited': 0,
            'last_active': None,
            'tickers': set(),
            'errors': 0,
            'email': None
        })
        
        # Process all entries
        for entry in data.get('data', []):
            usage = entry.get('usage', {})
            key = usage.get('key')
            tier = usage.get('tier')
            
            # Only analyze registered users
            if not key or tier != 'registered':
                continue
                
            # Update user stats
            user_activity[key]['total_requests'] += 1
            
            # Track successful requests
            if entry.get('related_requests'):
                for req in entry['related_requests']:
                    if req.get('status') == 'success':
                        user_activity[key]['successful_requests'] += 1
                    elif req.get('status') == 'rate_limited':
                        user_activity[key]['rate_limited'] += 1
            
            # Track tickers
            if usage.get('ticker'):
                user_activity[key]['tickers'].add(usage['ticker'])
            
            # Track errors
            if entry.get('error'):
                user_activity[key]['errors'] += 1
            
            # Update last active timestamp
            timestamp = usage.get('timestamp')
            if timestamp:
                dt = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
                if not user_activity[key]['last_active'] or dt > user_activity[key]['last_active']:
                    user_activity[key]['last_active'] = dt
        
        # Filter and sort candidates
        candidates = []
        for key, stats in user_activity.items():
            # Calculate upgrade score based on various factors
            score = (
                stats['total_requests'] * 0.4 +  # Weight for total usage
                stats['successful_requests'] * 0.3 +  # Weight for successful requests
                len(stats['tickers']) * 0.2 +  # Weight for variety of tickers
                (stats['rate_limited'] > 0) * 0.1  # Bonus for hitting rate limits
            )
            
            if score > 5:  # Minimum threshold for consideration
                # Resolve email for the key
                email = self.resolve_key_to_email(key)
                stats['email'] = email
                
                candidates.append({
                    'key': key,
                    'score': score,
                    'stats': stats
                })
        
        # Sort by score
        candidates.sort(key=lambda x: x['score'], reverse=True)
        
        if not candidates:
            print("No upgrade candidates found")
            return
        
        print("Top registered users showing high engagement:")
        for candidate in candidates[:5]:  # Show top 5 candidates
            stats = candidate['stats']
            print(f"\n🔑 Key: {candidate['key'][:8]}...")
            print(f"  • Email: {stats['email']}")
            print(f"  • Total Requests: {stats['total_requests']:,}")
            print(f"  • Successful Requests: {stats['successful_requests']:,}")
            print(f"  • Rate Limited: {stats['rate_limited']:,}")
            print(f"  • Unique Tickers: {len(stats['tickers']):,}")
            print(f"  • Errors: {stats['errors']:,}")
            if stats['last_active']:
                print(f"  • Last Active: {stats['last_active'].strftime('%Y-%m-%d %H:%M')}")
            print(f"  • Upgrade Score: {candidate['score']:.1f}")

    def run(self):
        """Run the complete monitoring report"""
        try:
            print("\n✅ Admin Monitoring Script – Output Summary\n")
            
            # Get and display usage summary
            usage_data = self.get_usage_summary()
            self.display_usage_summary(usage_data)
            
            # Get and display top keys
            self.display_top_keys(usage_data)
            
            # Get and display public IPs
            self.display_public_ips(usage_data)
            
            # Get and display errors
            self.display_errors(usage_data)
            
            # Display upgrade candidates
            self.display_upgrade_candidates(usage_data)
            
        except requests.exceptions.RequestException as e:
            print(f"Error accessing admin endpoints: {e}")
        except Exception as e:
            print(f"Unexpected error: {e}")

if __name__ == "__main__":
    try:
        monitor = AdminMonitor()
        monitor.run()
    except Exception as e:
        print(f"Fatal error: {e}")
