import os
import random
import time
from google import genai
from dotenv import load_dotenv

load_dotenv()

# Setup Gemini API client
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None

PROMPT_TEMPLATE = """Write 3 casual sentences to a freelancer.
Contact: {name}, {role}, {source}
Bio: {bio}
Angle: {trigger_angle_text}

Rules:
- Text only, no HTML/subject/greetings
- Start like texting a coworker
- Ban: streamline, optimize, leverage, synergy, chaos, minimalist, disruptive, bloated, revolutionary, game-changer
- Max 60 words
- Last line MUST be: auctron.in — completely free, no card needed"""

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
    
    try:
        from google.genai import types
        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=prompt,
            config=types.GenerateContentConfig(
                max_output_tokens=100, # Hard limit on output tokens
                temperature=0.7
            )
        )
        return response.text.strip(), angle_key
    except Exception as e:
        print(f"Error generating email for {contact.get('name')}: {e}")
        return f"ERROR: {str(e)}", "N/A"
    finally:
        # Free tier = 5 req/min → sleep 13s after every call to stay safely under
        time.sleep(13)
