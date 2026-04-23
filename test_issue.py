import os
from dotenv import load_dotenv
from google import genai

load_dotenv()
client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))

prompt = """
Give me a dramatic astrology reading for ZeroStar210.
Requirements:
- Start with "Eda ZeroStar210"
- Reference a Trivandrum location
- Use Manglish naturally (oola, potti, kili poyi)
- Write exactly ONE continuous sentence.
"""

response = client.models.generate_content(
    model="gemini-2.5-flash",
    contents=prompt,
    config={
        "temperature": 0.8,
    }
)

if not response.candidates:
    print("No candidates returned.")
else:
    c = response.candidates[0]
    print("Text:", repr(c.content.parts[0].text if c.content.parts else ""))
    print("Finish Reason:", c.finish_reason)
    if getattr(c, "safety_ratings", None):
        print("Safety Ratings:")
        for r in c.safety_ratings:
            print(f"  {r.category}: {r.probability}")
