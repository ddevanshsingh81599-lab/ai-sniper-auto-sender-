import os
import json
import time
from google import genai
from dotenv import load_dotenv

load_dotenv()

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None

EXTRACTION_PROMPT_TEMPLATE = """Read this freelancer profile carefully.
Extract specific information only.
Do not guess or assume anything.
If information is not clearly present write "unknown".

Profile data:
Name: {name}
Bio: {bio}
Role: {role}
Location: {location}
Source platform: {source}
Website: {website}

Extract and return ONLY this JSON:
{{
  "segment": "one of: indian_designer / indian_developer / global_designer / global_developer / indie_founder / freelance_writer / unknown",

  "painPoints": [
    "specific pain point from bio only",
    "another if mentioned",
    "maximum 3 points"
  ],

  "currentTools": [
    "tool name if mentioned in bio",
    "another tool if mentioned"
  ],

  "clientType": "one of: indian_clients / international_clients / both / unknown",

  "experience": "one of: junior / mid / senior / unknown",

  "bestAngle": "one of: gst_pain / email_delivery / ai_generator / simplicity / get_paid_faster / ui_feedback",

  "isFreelancer": true or false,

  "confidence": "one of: high / medium / low"
}}

Rules:
→ base everything on bio text only
→ never invent information
→ if bio is empty return all unknown
→ isFreelancer is false if bio suggests full time employment only
→ bestAngle based on:
   indian location → gst_pain
   designer role   → ui_feedback
   developer role  → ai_generator
   mentions payments/invoicing → get_paid_faster
   mentions email/spam         → email_delivery
   none of above               → simplicity

Return valid JSON only.
No explanation. No extra text."""


DEFAULT_EXTRACTION = {
    "segment":      "unknown",
    "painPoints":   "unknown",
    "currentTools": "unknown",
    "clientType":   "unknown",
    "experience":   "unknown",
    "bestAngle":    "simplicity",
    "isFreelancer": True,
    "confidence":   "low",
}


def extract_profile(contact: dict) -> dict:
    """
    Runs the Gemini profile extraction prompt on a raw contact dict.
    Returns a flat dict with the extracted fields as strings.
    Falls back to DEFAULT_EXTRACTION if AI fails or JSON is invalid.

    NOTE: This call does NOT add an extra sleep — it is always called
    immediately before generate_personalized_email() in main.py, which
    already has its own 13-second sleep in ai_agent.py. Adding a second
    sleep here keeps us safely under the free-tier 5 req/min cap without
    wasting extra time when both calls are sequential.
    """
    if not client:
        print("  ⚠️  Profile extractor: Gemini client not initialised (missing API key).")
        return DEFAULT_EXTRACTION.copy()

    prompt = EXTRACTION_PROMPT_TEMPLATE.format(
        name=contact.get("name", "")[:60],
        bio=contact.get("bio", "")[:400],      # keep prompt small
        role=contact.get("role", "")[:60],
        location=contact.get("location", "unknown"),
        source=contact.get("source", "unknown"),
        website=contact.get("url", "none") or "none",
    )

    try:
        from google.genai import types
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
            config=types.GenerateContentConfig(
                max_output_tokens=300,
                temperature=0.2,      # low temp → consistent structured output
            ),
        )
        raw = response.text.strip()

        # Strip markdown fences if the model wrapped the JSON
        raw = raw.replace("```json", "").replace("```", "").strip()

        extracted = json.loads(raw)

        # Flatten list fields into pipe-delimited strings for Sheets
        pain_points = extracted.get("painPoints", [])
        if isinstance(pain_points, list):
            pain_points_str = " | ".join(str(p) for p in pain_points if p)
        else:
            pain_points_str = str(pain_points)

        current_tools = extracted.get("currentTools", [])
        if isinstance(current_tools, list):
            current_tools_str = " | ".join(str(t) for t in current_tools if t)
        else:
            current_tools_str = str(current_tools)

        return {
            "segment":      extracted.get("segment", "unknown"),
            "painPoints":   pain_points_str or "unknown",
            "currentTools": current_tools_str or "unknown",
            "clientType":   extracted.get("clientType", "unknown"),
            "experience":   extracted.get("experience", "unknown"),
            "bestAngle":    extracted.get("bestAngle", "simplicity"),
            "isFreelancer": bool(extracted.get("isFreelancer", True)),
            "confidence":   extracted.get("confidence", "low"),
        }

    except (json.JSONDecodeError, Exception) as e:
        print(f"  ⚠️  Profile extractor failed for {contact.get('name', '?')}: {e}")
        return DEFAULT_EXTRACTION.copy()
    finally:
        # Free tier: 5 req/min → 13 s gap keeps us under the cap
        # (ai_agent.py has its own identical sleep for the email call)
        time.sleep(13)
