"""Debug: see exactly what Gemini returns."""
import os, time
from google import genai
from google.genai import types
from dotenv import load_dotenv

load_dotenv()

client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))

prompt = """You are writing a cold outreach email for Auctron, a free invoicing tool for freelancers.

Contact info:
  Name: Devansh Singh
  Role: Full-Stack Developer & Founder
  Found on: LinkedIn
  Bio: Building SaaS tools. Freelances on the side doing web dev projects for Indian SMBs. Uses Zoho Invoice currently.

Your angle: Mention stopping $30/mo subs just for PDFs

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

print("Sending prompt to Gemini...")
response = client.models.generate_content(
    model='gemini-2.5-flash',
    contents=prompt,
    config=types.GenerateContentConfig(
        max_output_tokens=250,
        temperature=0.7
    )
)

print(f"\n--- RAW response.text (type={type(response.text)}) ---")
print(repr(response.text))
print(f"--- Length: {len(response.text) if response.text else 0} ---")

print(f"\n--- Candidates ---")
for i, c in enumerate(response.candidates):
    print(f"Candidate {i}: finish_reason={c.finish_reason}")
    for p in c.content.parts:
        print(f"  Part text: {repr(p.text)}")
        print(f"  Part thought: {getattr(p, 'thought', 'N/A')}")
