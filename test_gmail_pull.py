"""
test_gmail_pull.py — Standalone script to test Gmail IMAP connection and TOS scan extraction.

Usage:
  1. Via Environment Variables:
       $env:GMAIL_USER="your_email@gmail.com"
       $env:GMAIL_APP_PASSWORD="xxxx xxxx xxxx xxxx"
       python test_gmail_pull.py

  2. Via Command Line Arguments:
       python test_gmail_pull.py --user "your_email@gmail.com" --password "xxxx xxxx xxxx xxxx"

  3. Dry-Run / Lookback Customization:
       python test_gmail_pull.py --days 7 --sender "thinkorswim.com"
"""
import os
import sys
import argparse

# Enable UTF-8 console output on Windows
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

# Add repo root to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

def main():
    parser = argparse.ArgumentParser(description="Test ThinkOrSwim Gmail IMAP Ingestion")
    parser.add_argument("--user", help="Gmail address (overrides GMAIL_USER env var)")
    parser.add_argument("--password", help="Google App Password (overrides GMAIL_APP_PASSWORD env var)")
    parser.add_argument("--days", type=int, default=3, help="Lookback window in days (default: 3)")
    parser.add_argument("--sender", default="thinkorswim.com", help="Email sender filter (default: thinkorswim.com)")
    parser.add_argument("--subject", default="Alert: New symbol", help="Subject filter (default: 'Alert: New symbol')")
    args = parser.parse_args()

    # Override environment if flags provided
    if args.user:
        os.environ["GMAIL_USER"] = args.user.strip()
    if args.password:
        os.environ["GMAIL_APP_PASSWORD"] = args.password.strip()
    if args.days:
        os.environ["TOS_LOOKBACK_DAYS"] = str(args.days)
    if args.sender:
        os.environ["TOS_EMAIL_FROM"] = args.sender.strip()
    if args.subject:
        os.environ["TOS_EMAIL_SUBJECT"] = args.subject.strip()

    from backend.services.gmail_watchlist import (
        poll_and_store, fetch_today_watchlist, get_watchlist_status
    )

    gmail_user = os.getenv("GMAIL_USER", "").strip()
    gmail_pwd = os.getenv("GMAIL_APP_PASSWORD", "").strip()

    print("=" * 60)
    print("[StockPulse] ThinkOrSwim Gmail IMAP Tester")
    print("=" * 60)
    print(f"* User:       {gmail_user if gmail_user else '(NOT SET)'}")
    print(f"* Password:   {'*' * len(gmail_pwd) if gmail_pwd else '(NOT SET)'}")
    print(f"* Sender:     {args.sender}")
    print(f"* Subject:    {args.subject}")
    print(f"* Lookback:   {args.days} days")
    print("=" * 60)

    if not gmail_user or not gmail_pwd:
        print("\n[!] GMAIL_USER or GMAIL_APP_PASSWORD is missing!")
        print("\nTo generate a Google App Password (takes 30 seconds):")
        print("  1. Go to your Google Account -> Security (https://myaccount.google.com/security)")
        print("  2. Ensure 2-Step Verification is ON")
        print("  3. Go to 'App Passwords' (https://myaccount.google.com/apppasswords)")
        print("  4. Create a new App Password named 'StockPulse'")
        print("  5. Copy the 16-character code (e.g. abcd efgh ijkl mnop)\n")
        print("Then test with:")
        print("  python test_gmail_pull.py --user \"you@gmail.com\" --password \"your-16-char-code\"")
        print("\nOr set in PowerShell:")
        print("  $env:GMAIL_USER=\"you@gmail.com\"")
        print("  $env:GMAIL_APP_PASSWORD=\"your-16-char-code\"")
        print("  python test_gmail_pull.py")
        return

    print("\nConnecting to imap.gmail.com:993 and searching for alerts...")
    new_count = poll_and_store()
    print(f"Pulled {new_count} new email(s).")

    status = get_watchlist_status()
    print("\nCurrent Watchlist Status:")
    print(f"* Total Saved Messages: {status.get('total_messages')}")
    print(f"* Active Tickers Count:  {status.get('count')}")
    print(f"* Latest Alert Time:    {status.get('latest_date')} {status.get('latest_time')}")

    tickers = fetch_today_watchlist()
    if tickers:
        print(f"\n[OK] Found {len(tickers)} Tickers Ready for Scanner:")
        print("   " + ", ".join(tickers))
    else:
        print("\n[!] No tickers found for today or recent lookback. Check if TOS emails are present in your inbox.")

if __name__ == "__main__":
    main()
