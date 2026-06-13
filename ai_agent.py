import os
import random
import time
from google import genai
from dotenv import load_dotenv

load_dotenv()

# Setup Gemini API client
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None

PROMPT_TEMPLATE = """You are writing a cold outreach email for Auctron, a free invoicing tool for freelancers.

Contact info:
  Name: {name}
  Role: {role}
  Found on: {source}
  Bio: {bio}

Your angle: {trigger_angle_text}

Write the email in this EXACT format:

Hey [first name],

[One casual opening sentence that references their role or bio — like texting a colleague.]

Here's what caught my eye:
- [bullet 1: a specific pain point they likely have, tied to the angle]
- [bullet 2: how Auctron solves it]
- [bullet 3: optional bonus benefit]

auctron.in — completely free, no card needed.

HARD RULES:
1. Plain text only — NO HTML, NO markdown bold/italic
2. Keep it under 80 words total
3. NEVER use these words: streamline, optimize, leverage, synergy, chaos, minimalist, disruptive, bloated, revolutionary, game-changer
4. Sound human — like a DM, not a sales pitch
5. The bullet points MUST start with "- "
6. Always end with: auctron.in — completely free, no card needed"""

TRIGGER_ANGLES = {
    "A": "Ask for honest UI feedback (they're a dev/designer)",
    "B": "Mention stopping $30/mo subs just for PDFs",
    "C": "Focus on getting paid fast without complex tools"
}

def generate_personalized_email(contact: dict) -> tuple[str, str]:
    if not client:
        return "ERROR: Gemini API Key not set.", "N/A"
        
    angle_key = random.choice(list(TRIGGER_ANGLES.keys()))
    
    prompt = PROMPT_TEMPLATE.format(
        name=contact.get("name", "Freelancer")[:30],
        role=contact.get("role", "Developer")[:30],
        bio=contact.get("bio", "")[:80], # aggressive bio truncation
        source=contact.get("source", "Web"),
        trigger_angle_text=TRIGGER_ANGLES[angle_key]
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
                    temperature=0.7,
                    thinking_config=types.ThinkingConfig(
                        thinking_budget=0  # disable thinking — we just need a short email
                    )
                )
            )
            text = response.text.strip()
            
            # Quality check: must have bullet points and reasonable length
            if len(text) < 50 or "- " not in text:
                print(f"  ⚠️  Short/bad output (attempt {attempt+1}), retrying...")
                time.sleep(5)
                continue
            
            return text, angle_key
        except Exception as e:
            print(f"Error generating email for {contact.get('name')}: {e}")
            return f"ERROR: {str(e)}", "N/A"
        finally:
            # Free tier = 5 req/min → sleep 13s after every call to stay safely under
            time.sleep(13)
    
    # All retries exhausted — return whatever we got
    return "PENDING", "N/A"

