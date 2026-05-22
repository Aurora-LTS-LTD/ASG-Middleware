"""
ASG Solutions — Make.com Integration Service
==============================================
This file handles sending data to Make.com via webhooks.

REAL-WORLD ANALOGY:
Make.com is like a smart assistant that sits between your app and
the outside world. When something happens (a customer sends a message,
an invoice is ready), you tell Make.com, and it handles the rest:
sending WhatsApp messages, processing AI responses, etc.

A "webhook" is like a doorbell — you ring it (send an HTTP POST),
and Make.com wakes up and does its job.

IMPORTANT:
To use this, you need a Make.com webhook URL in your .env file:
  MAKE_WEBHOOK_URL=https://hook.make.com/your-scenario-id
"""

# ─────────────────────────────────────────────────────────────
# IMPORTS
# ─────────────────────────────────────────────────────────────
import os

# "httpx" — a modern HTTP client for Python (like a web browser
#   that can send requests programmatically). We use it to POST
#   data to Make.com's webhook URL.
# "AsyncClient" — the async version, so we don't block the server
#   while waiting for Make.com to respond.
import httpx


# ─────────────────────────────────────────────────────────────
# CONFIGURATION
# ─────────────────────────────────────────────────────────────
# Read the Make.com webhook URL from the .env file.
# If not set, it will be None and the function will skip the call.
MAKE_WEBHOOK_URL = os.getenv("MAKE_WEBHOOK_URL")


# ─────────────────────────────────────────────────────────────
# FUNCTION: send_to_make
# ─────────────────────────────────────────────────────────────
# PURPOSE:
#   Send data to Make.com via an HTTP POST request.
#
# REAL-WORLD ANALOGY:
#   Imagine putting a letter in an envelope (the JSON payload),
#   writing the address (the webhook URL), and dropping it in the
#   mailbox (sending the POST request). Make.com receives the
#   letter and processes it.
#
# PARAMETERS:
#   sender (str)      — who sent the message (e.g., phone number)
#   message (str)     — the content to send
#   business_id (str) — which business this relates to (optional)
#
# RETURNS:
#   dict — the response from Make.com, or None if something failed
# ─────────────────────────────────────────────────────────────
async def send_to_make(
    sender: str,
    message: str,
    business_id: str = None,
) -> dict | None:
    """
    Send data to Make.com via webhook POST request.

    Args:
        sender:      The sender identifier (phone number or "system").
        message:     The message content or formatted data.
        business_id: Optional business ID for routing.

    Returns:
        Response dict from Make.com, or None on failure.
    """

    # ── Step 1: Check if the webhook URL is configured ──
    if not MAKE_WEBHOOK_URL:
        print("[MAKE.COM] ⚠️  MAKE_WEBHOOK_URL not set in .env — skipping")
        print("[MAKE.COM] To enable, add MAKE_WEBHOOK_URL=https://hook.make.com/... to .env")
        return None

    # ── Step 2: Build the payload ──
    # The "payload" is the data package we send to Make.com.
    # It's a Python dictionary that gets converted to JSON automatically.
    payload = {
        "sender": sender,
        "message": message,
        "business_id": business_id,
    }

    print(f"[MAKE.COM] Sending to Make.com...")
    print(f"[MAKE.COM] Sender: {sender} | Business: {business_id}")

    # ── Step 3: Send the HTTP POST request ──
    try:
        # "async with" creates an HTTP client that automatically
        # cleans up after itself (closes connections).
        # "timeout=30.0" means: if Make.com doesn't respond in 30
        # seconds, give up and raise a timeout error.
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                MAKE_WEBHOOK_URL,
                json=payload,  # "json=" automatically converts dict to JSON
            )

            # "raise_for_status()" checks if the response code is
            # an error (4xx or 5xx). If so, it raises an exception.
            response.raise_for_status()

            # ── Step 4: Parse the response ──
            try:
                result = response.json()
            except Exception:
                # Make.com sometimes returns plain text instead of JSON
                result = {"raw_response": response.text}

            print(f"[MAKE.COM] ✅ Success! Response: {result}")
            return result

    # ── Error Handling ──
    # Each type of error gets its own handler with a clear message.

    except httpx.TimeoutException:
        # Make.com didn't respond within 30 seconds
        print(f"[MAKE.COM] ❌ Timeout — Make.com didn't respond in 30 seconds")
        return None

    except httpx.ConnectError:
        # Can't reach Make.com (network issue or wrong URL)
        print(f"[MAKE.COM] ❌ Connection error — can't reach Make.com")
        print(f"[MAKE.COM]    Check your MAKE_WEBHOOK_URL and internet connection")
        return None

    except httpx.HTTPStatusError as e:
        # Make.com returned an error (4xx or 5xx status code)
        print(f"[MAKE.COM] ❌ HTTP error {e.response.status_code}: {e.response.text}")
        return None

    except Exception as e:
        # Catch-all for any unexpected error
        print(f"[MAKE.COM] ❌ Unexpected error: {type(e).__name__}: {e}")
        return None
