#!/usr/bin/env python3
"""
Zerodha Kite Connect Authentication Helper Script.
This script helps you generate a request_token and access_token using your
KITE_API_KEY and KITE_API_SECRET.

Usage:
    python scripts/generate_kite_tokens.py
"""

import os
import re
import sys
import hashlib
import json
import webbrowser
import urllib.request
import urllib.parse
from urllib.error import HTTPError, URLError

def load_env_vars():
    """Loads KITE_API_KEY and KITE_API_SECRET from the .env file in the project root."""
    api_key = ""
    api_secret = ""
    
    # Locate .env by walking up from the current directory
    current_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(current_dir)
    env_path = os.path.join(project_root, ".env")
    
    if os.path.exists(env_path):
        print(f"[*] Reading credentials from {env_path}...")
        try:
            with open(env_path, "r", encoding="utf-8") as f:
                content = f.read()
                
            for line in content.splitlines():
                line = line.strip()
                if line.startswith("#") or "=" not in line:
                    continue
                parts = line.split("=", 1)
                if len(parts) == 2:
                    key = parts[0].strip()
                    val = parts[1].strip().strip('"').strip("'")
                    if key == "KITE_API_KEY":
                        api_key = val
                    elif key == "KITE_API_SECRET":
                        api_secret = val
        except Exception as e:
            print(f"[!] Error reading .env file: {e}")
            
    return env_path, api_key, api_secret

def update_env_file(env_path, request_token, access_token):
    """Updates the .env file with the newly generated request and access tokens."""
    if not os.path.exists(env_path):
        print(f"[!] .env file not found at {env_path}. Skipping update.")
        return False
        
    try:
        with open(env_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
            
        updated_request = False
        updated_access = False
        new_lines = []
        
        for line in lines:
            trimmed = line.strip()
            if trimmed.startswith("KITE_REQUEST_TOKEN="):
                new_lines.append(f"KITE_REQUEST_TOKEN={request_token}\n")
                updated_request = True
            elif trimmed.startswith("KITE_ACCESS_TOKEN="):
                new_lines.append(f"KITE_ACCESS_TOKEN={access_token}\n")
                updated_access = True
            else:
                new_lines.append(line)
                
        # If they weren't in the file, append them
        if not updated_request:
            new_lines.append(f"KITE_REQUEST_TOKEN={request_token}\n")
        if not updated_access:
            new_lines.append(f"KITE_ACCESS_TOKEN={access_token}\n")
            
        with open(env_path, "w", encoding="utf-8") as f:
            f.writelines(new_lines)
            
        print(f"[+] Successfully updated {env_path} with new tokens.")
        return True
    except Exception as e:
        print(f"[!] Failed to update .env file: {e}")
        return False

def main():
    print("=========================================================")
    print("      Zerodha Kite Connect Authentication Helper         ")
    print("=========================================================")
    
    env_path, api_key, api_secret = load_env_vars()
    
    if not api_key:
        api_key = input("Enter your KITE_API_KEY: ").strip()
    else:
        print(f"[+] Loaded KITE_API_KEY: {api_key}")
        
    if not api_secret:
        api_secret = input("Enter your KITE_API_SECRET: ").strip()
    else:
        print(f"[+] Loaded KITE_API_SECRET: [MASKED]")
        
    if not api_key or not api_secret:
        print("[!] API Key and API Secret are required. Exiting.")
        sys.exit(1)
        
    # Generate login URL
    login_url = f"https://kite.zerodha.com/connect/login?v=3&api_key={api_key}"
    
    print("\n---------------------------------------------------------")
    print("Step 1: Authenticate with Zerodha")
    print("---------------------------------------------------------")
    print("Please visit the following URL to log in and authorize your app:")
    print(f"\n👉  {login_url}\n")
    
    try:
        print("[*] Attempting to open browser automatically...")
        webbrowser.open(login_url)
    except Exception:
        print("[!] Could not open browser automatically. Please copy & paste the URL above.")
        
    print("\nAfter logging in, you will be redirected to your configured Redirect URL.")
    print("The redirected URL in your browser's address bar will look like:")
    print("   https://your-redirect-url.com/?request_token=XXXXXX&action=login&status=success")
    
    print("\n---------------------------------------------------------")
    print("Step 2: Enter Redirect URL / Request Token")
    print("---------------------------------------------------------")
    user_input = input("Paste the FULL redirect URL or the raw request_token here: ").strip()
    
    if not user_input:
        print("[!] Input cannot be empty. Exiting.")
        sys.exit(1)
        
    # Extract request_token
    request_token = ""
    if "request_token=" in user_input:
        match = re.search(r"request_token=([a-zA-Z0-9]+)", user_input)
        if match:
            request_token = match.group(1)
            print(f"[+] Parsed request_token: {request_token}")
        else:
            print("[!] Could not parse request_token from the URL. Trying raw input.")
            request_token = user_input
    else:
        request_token = user_input
        print(f"[+] Using raw request_token input: {request_token}")
        
    # Compute checksum: SHA-256(api_key + request_token + api_secret)
    raw_checksum = f"{api_key}{request_token}{api_secret}"
    checksum = hashlib.sha256(raw_checksum.encode("utf-8")).hexdigest()
    
    print("\n---------------------------------------------------------")
    print("Step 3: Exchanging Request Token for Access Token")
    print("---------------------------------------------------------")
    
    url = "https://api.kite.trade/session/token"
    post_data = urllib.parse.urlencode({
        "api_key": api_key,
        "request_token": request_token,
        "checksum": checksum
    }).encode("utf-8")
    
    req = urllib.request.Request(url, data=post_data, method="POST")
    req.add_header("X-Kite-Version", "3")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    
    try:
        with urllib.request.urlopen(req) as response:
            res_body = response.read().decode("utf-8")
            res_json = json.loads(res_body)
            
            if res_json.get("status") == "success":
                data = res_json.get("data", {})
                access_token = data.get("access_token", "")
                user_id = data.get("user_id", "")
                user_name = data.get("user_name", "")
                
                print("[✔] Authentication Successful!")
                print(f"    User      : {user_name} ({user_id})")
                print(f"    Req Token : {request_token}")
                print(f"    Access Tok: {access_token}")
                print("\n=========================================================")
                print("Important: The access token is valid until 06:00 AM IST tomorrow.")
                print("=========================================================")
                
                # Ask to write to .env
                confirm = input("\nWould you like to save these tokens to your .env file? [Y/n]: ").strip().lower()
                if confirm in ("", "y", "yes"):
                    update_env_file(env_path, request_token, access_token)
                else:
                    print("[*] Did not write to .env. You can add these manually:")
                    print(f"KITE_REQUEST_TOKEN={request_token}")
                    print(f"KITE_ACCESS_TOKEN={access_token}")
            else:
                error_msg = res_json.get("message", "Unknown Kite API error")
                print(f"[❌] Exchange failed: {error_msg}")
                
    except HTTPError as e:
        error_body = e.read().decode("utf-8")
        try:
            err_json = json.loads(error_body)
            error_msg = err_json.get("message", error_body)
        except Exception:
            error_msg = error_body
        print(f"[❌] HTTP Error {e.code}: {error_msg}")
    except URLError as e:
        print(f"[❌] Network Connection Error: {e.reason}")
    except Exception as e:
        print(f"[❌] Unexpected Error: {e}")

if __name__ == "__main__":
    main()
