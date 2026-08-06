import json
from groq import Groq
from dotenv import load_dotenv
import os

load_dotenv()
my_key = os.environ.get("GROQ_API_KEY")

client = Groq(api_key = my_key)
worker_message = "I did a night shift on 2024-08-12. Worked exactly 8 hours and 30 minutes. My hourly rate is "\
                 "15 Rupees, but the boss only gave me 150 Rupees total."
system_prompt = "Extract: shift_date (YYYY-MM-DD), extracted_hours (int), extracted_minutes (int), rate (float), " \
                "actual_payout (float). Respond ONLY with valid JSON." \

response = client.chat.completions.create(
    model = "llama-3.3-70b-versatile",
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": worker_message}
    ],
    response_format = {"type": "json_object"},
    temperature = 0.0
)
ai_json_string = response.choices[0].message.content
print(ai_json_string)
parsed_dict = json.loads(ai_json_string)
print(parsed_dict)
