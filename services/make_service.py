# =============================================================================
# make_service.py  --  The "Messenger" that talks to Make.com
# =============================================================================
#
# REAL-WORLD ANALOGY:
# Imagine your middleware (FastAPI) is a receptionist at a hotel.
# A guest (WhatsApp user) walks in and says something.
# The receptionist writes down the message on a slip of paper
# and hands it to a runner (this file) who carries it to the
# manager's office (Make.com).  The manager processes the request
# and the runner brings back a response.
#
# This file IS that runner.  Its only job:
#   1. Take the message details (who sent it, what they said, which business).
#   2. Pack them into a neat JSON envelope.
#   3. Deliver the envelope to Make.com via an HTTP POST request.
#   4. Bring back whatever Make.com responds with -- or report that
#      something went wrong.
# =============================================================================

# ---------------------------------------------------------------------------
# IMPORTS  --  tools we need from Python's toolbox
# ---------------------------------------------------------------------------

import os          # "os" lets us read environment variables -- secret settings
                   # stored outside our code so passwords/URLs don't leak.

import httpx       # "httpx" is a modern HTTP library for Python.
                   # Think of it as Python's postal service -- it can send
                   # letters (requests) to any address (URL) on the internet
                   # and bring back the reply.
                   # We use the *async* version so our server isn't stuck
                   # waiting -- it can serve other guests while the runner
                   # is on the way to Make.com.

# ---------------------------------------------------------------------------
# CONFIGURATION  --  read the Make.com webhook URL from the environment
# ---------------------------------------------------------------------------
# WHY an environment variable?
#   Storing secrets (URLs, passwords, API keys) directly in code is like
#   writing your ATM PIN on the back of your debit card.  Anyone who sees
#   the code sees the secret.  Instead, we store it in a ".env" file or
#   set it in the terminal, and read it here with os.getenv().
#
# HOW TO SET IT (in terminal, before running the server):
#   export MAKE_WEBHOOK_URL="https://hook.make.com/your-unique-id-here"
#
# Or put it in a .env file and load it with python-dotenv (a future step).
# ---------------------------------------------------------------------------

MAKE_WEBHOOK_URL = os.getenv("MAKE_WEBHOOK_URL")
# os.getenv("MAKE_WEBHOOK_URL") looks for a variable called MAKE_WEBHOOK_URL
# in the system environment.  If it exists, we get its value (a URL string).
# If it does NOT exist, we get None (Python's way of saying "nothing here").


# ---------------------------------------------------------------------------
# THE MAIN FUNCTION  --  send a message payload to Make.com
# ---------------------------------------------------------------------------

async def send_to_make(sender: str, message: str, business_id: str = None):
    """
    Send a user's message to Make.com for AI processing.

    ANALOGY:
        This function is the runner.  You hand it three things:
          - sender      : the guest's phone number (who is talking)
          - message     : what the guest said
          - business_id : which business/branch the message belongs to
                          (optional -- like a department code)
        The runner packs them into an envelope, walks to Make.com,
        and comes back with whatever Make.com replied.

    Parameters
    ----------
    sender : str
        The phone number (or user ID) of the person who sent the message.
        Example: "+972501234567"

    message : str
        The actual text the user typed.
        Example: "I want to book a table for 4 at 8pm"

    business_id : str, optional
        An identifier for the business this message is related to.
        If you run bots for multiple businesses, this tells Make.com
        which business logic to use.  Defaults to None (not provided).

    Returns
    -------
    dict or None
        - On SUCCESS: returns the data Make.com sent back (as a dictionary).
        - On FAILURE: returns None, so the caller knows something went wrong.
    """

    # --- Step 1: Check that we actually have a URL to send to ---------------
    # If MAKE_WEBHOOK_URL was never set, there's nowhere to send the message.
    # That's like telling the runner "go deliver this" but not giving an address.

    if not MAKE_WEBHOOK_URL:
        print("[make_service] ERROR: MAKE_WEBHOOK_URL is not set!")
        print("  -> Set it with:  export MAKE_WEBHOOK_URL='https://hook.make.com/...'")
        return None  # Give up early -- return "nothing" to the caller.

    # --- Step 2: Build the JSON payload (the envelope contents) -------------
    # JSON = JavaScript Object Notation.  It's a universal format for
    # packaging data so any system can read it -- like using a standard
    # envelope size that every post office in the world accepts.

    payload = {
        "sender": sender,           # Who sent the message
        "message": message,         # What they said
        "business_id": business_id  # Which business (can be None)
    }

    print(f"[make_service] Preparing to send to Make.com...")
    print(f"  -> Sender     : {sender}")
    print(f"  -> Message    : {message}")
    print(f"  -> Business ID: {business_id}")

    # --- Step 3: Send the HTTP POST request ---------------------------------
    # We use a "try / except" block here.  ANALOGY:
    #   "Try to deliver the letter.  If anything goes wrong on the way
    #    (road is blocked, office is closed, runner got lost), catch the
    #    problem and report it instead of crashing the whole hotel."
    #
    # httpx.AsyncClient() opens a temporary connection to the internet.
    # "async with" makes sure the connection is properly closed when done,
    # even if an error happens -- like always hanging up the phone, even
    # if the call drops.

    try:
        async with httpx.AsyncClient() as client:
            # client.post() sends a POST request.
            # - url     : WHERE to send it (the Make.com webhook address)
            # - json    : WHAT to send (our payload, auto-converted to JSON)
            # - timeout : HOW LONG to wait before giving up (30 seconds).
            #             If Make.com doesn't reply in 30s, we assume
            #             something is wrong and stop waiting.

            response = await client.post(
                url=MAKE_WEBHOOK_URL,
                json=payload,
                timeout=30.0   # seconds -- generous but not infinite
            )

        # --- Step 4: Check the response -------------------------------------
        # HTTP status codes are like short reports from the post office:
        #   200 = "Delivered successfully!"
        #   400 = "Bad address or bad envelope format"
        #   500 = "The office had an internal problem"
        #
        # response.raise_for_status() will THROW an error if the status
        # code means something went wrong (anything 400 or above).

        response.raise_for_status()

        # If we get here, the request was successful (status 200-299).
        print(f"[make_service] SUCCESS! Make.com responded with status {response.status_code}")

        # Try to parse the response body as JSON.
        # Make.com usually sends back JSON, but just in case it sends
        # plain text, we handle both situations.

        try:
            response_data = response.json()  # Try to read as JSON (dict)
        except Exception:
            # If the body isn't valid JSON, just grab the raw text instead.
            response_data = {"raw_response": response.text}

        print(f"[make_service] Response data: {response_data}")
        return response_data  # Hand the reply back to whoever called us.

    # --- Error Handling (the "except" blocks) --------------------------------
    # Each "except" catches a SPECIFIC type of problem so we can print
    # a helpful message.  Like a doctor diagnosing symptoms -- different
    # symptoms, different diagnosis.

    except httpx.TimeoutException:
        # The runner waited too long and Make.com never answered.
        # Maybe Make.com is overloaded or our internet is slow.
        print("[make_service] ERROR: Request to Make.com timed out (took longer than 30 seconds).")
        print("  -> Make.com might be busy or your internet connection is slow.")
        return None

    except httpx.ConnectError:
        # Couldn't even reach Make.com -- like the road to the office is closed.
        # Usually means no internet or the URL is wrong.
        print("[make_service] ERROR: Could not connect to Make.com.")
        print(f"  -> Check your internet and verify the URL: {MAKE_WEBHOOK_URL}")
        return None

    except httpx.HTTPStatusError as e:
        # We reached Make.com but it sent back an error status code.
        # "e" contains details about what went wrong.
        print(f"[make_service] ERROR: Make.com returned an error status.")
        print(f"  -> Status code: {e.response.status_code}")
        print(f"  -> Response body: {e.response.text}")
        return None

    except Exception as e:
        # A catch-all for any OTHER unexpected error we didn't predict.
        # Like a safety net under a trapeze -- hopefully never needed,
        # but there just in case.
        print(f"[make_service] ERROR: An unexpected error occurred: {e}")
        return None
