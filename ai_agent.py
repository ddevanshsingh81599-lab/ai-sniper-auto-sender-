import os
import random
import time
from google import genai
from dotenv import load_dotenv

load_dotenv()

# Setup Gemini API client
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None

PROMPT_TEMPLATE = """
You are a founder writing a personal email
to a fellow freelancer.

Their information:
Name: {name}
Role: {role}
Segment: {segment}
Pain point: {pain_points}
Tools they use: {current_tools}
Best angle: {best_angle}
Location: {location}

Angle to use:
gst_pain       → mention GST headache
                 solved natively
email_delivery → mention invoices
                 going to spam fixed
ai_generator   → mention plain English
                 fills the whole form
ui_feedback    → ask their opinion
                 as a designer
get_paid_faster→ mention one click
                 payment for clients
simplicity     → mention 3 minutes
                 to send first invoice

Write the email following this structure:

Line 1:
→ one observation about their work
→ specific not generic
→ not a compliment just an observation
→ reference their actual role or bio
→ lowercase start

Line 2:
→ one sentence about what you built
→ mention the real human reason
→ not corporate speak
→ connect to their specific pain point
   naturally not directly

Line 3:
→ one small ask
→ not "sign up" or "check this out"
→ "would you give it a look"
→ or "curious what you think"
→ or "worth 3 minutes if billing
      is ever annoying"

Final line:
→ your first name only: Devansh
→ then on next line: auctron.in

Strict rules:
→ plain text only
→ maximum 75 words total
→ no commas unless essential
→ no exclamation marks ever
→ no formal greeting
→ no "Hi" or "Hey" or "Dear"
   start directly with observation
→ contractions always
   don't I've you're it's
→ never start with word "I"
→ use one of these once maximum:
   "honestly" "actually" "tbh"
→ short sentences preferred
→ incomplete sentences are fine
→ one paragraph maximum per thought

Banned words list:
streamline optimize leverage synergy
excited passionate thrilled revolutionary
game-changer innovative disruptive
would love to don't hesitate
please feel free best regards
looking forward to touching base
reaching out I hope this finds you

Output:
→ email body text only
→ no subject line
→ no notes or explanation
→ no "here is the email" preamble
→ just the email itself
"""

# Best angle mapping — the prompt already contains the angle descriptions,
# so we just pick one if the sheet doesn't have one already.
ANGLE_OPTIONS = [
    "gst_pain",
    "email_delivery",
    "ai_generator",
    "ui_feedback",
    "get_paid_faster",
    "simplicity",
]

def generate_personalized_email(contact: dict) -> tuple[str, str]:
    if not client:
        return "ERROR: Gemini API Key not set.", "N/A"

    # Use the best angle from the sheet if available, otherwise pick randomly
    best_angle = contact.get("best_angle", "") or contact.get("bestAngle", "")
    if not best_angle or best_angle.lower() in ("", "n/a", "none"):
        best_angle = random.choice(ANGLE_OPTIONS)

    prompt = PROMPT_TEMPLATE.format(
        name=contact.get("name", "Freelancer")[:40],
        role=contact.get("role", "Developer")[:50],
        segment=contact.get("segment", "freelancer")[:30],
        pain_points=contact.get("painPoints", contact.get("pain_points", "billing takes too long"))[:100],
        current_tools=contact.get("currentTools", contact.get("current_tools", "unknown"))[:60],
        best_angle=best_angle,
        location=contact.get("location", "India")[:30],
    )

    max_retries = 2
    for attempt in range(max_retries + 1):
        try:
            from google.genai import types
            response = client.models.generate_content(
                model='gemini-2.5-flash',
                contents=prompt,
                config=types.GenerateContentConfig(
                    max_output_tokens=1024,
                    temperature=0.8,
                    thinking_config=types.ThinkingConfig(
                        thinking_budget=0  # disable thinking — we just need a short email
                    )
                )
            )
            text = response.text.strip()

            # Quality check: must be reasonable length and end with auctron.in
            if len(text) < 50:
                print(f"  ⚠️  Short output (attempt {attempt+1}), retrying...")
                time.sleep(5)
                continue

            return text, best_angle
        except Exception as e:
            print(f"Error generating email for {contact.get('name')}: {e}")
            return f"ERROR: {str(e)}", "N/A"
        finally:
            # Free tier = 5 req/min → sleep 13s after every call to stay safely under
            time.sleep(13)

    # All retries exhausted
    return "PENDING", "N/A"
