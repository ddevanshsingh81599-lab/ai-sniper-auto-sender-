from ai_agent import generate_personalized_email

test_contact = {
    "name": "Abhiraj",
    "role": "Freelance UI Designer",
    "bio": "I design beautiful and modern user interfaces for SaaS startups.",
    "source": "Peerlist"
}

print("Running Gemini AI test...")
email_text, angle = generate_personalized_email(test_contact)

print("\n--- GENERATED EMAIL ---")
print(email_text)
print("-----------------------")
print(f"Trigger Angle Used: {angle}")
