"""Quick test: generate a personalized email with new prompt and send it."""

from ai_agent import generate_personalized_email
from gmail_sender import send_email

# Simulate a real contact with the full profile data
test_contact = {
    "name": "Devansh Singh",
    "role": "Full-Stack Developer & Founder",
    "segment": "indian_developer",
    "painPoints": "GST compliance | late payments from clients",
    "currentTools": "Zoho Invoice",
    "bestAngle": "gst_pain",
    "location": "Delhi, India",
    "bio": "Building SaaS tools. Freelances on the side doing web dev projects for Indian SMBs.",
}

print("🧪 Generating personalized email with new prompt...")
body, angle = generate_personalized_email(test_contact)

print(f"\n--- Generated Email (Angle: {angle}) ---")
print(body)
print("--- End ---\n")

subject = "quick thing about invoicing"

print(f"📤 Sending to ddevanshsingh945@gmail.com ...")
ok = send_email("ddevanshsingh945@gmail.com", subject, body)

if ok:
    print("✅ Sent! Check your inbox.")
else:
    print("❌ Failed to send.")
